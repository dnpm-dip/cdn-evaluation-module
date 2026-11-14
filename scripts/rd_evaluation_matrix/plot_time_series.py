#!/usr/bin/env -S uv run --script
# /// script
# dependencies = ["matplotlib"]
# ///
"""
Plot the time series of rd_time_series.py from its CSV: one page per figure with small multiples,
a panel per Leistungserbringer (LE), written as one PDF and one PNG per page.

Each panel shows the curve of its LE over the periods and, for percentages and durations, the
total as a thin reference line. The panels are ordered by the value of the LE over the whole
period (--order level), by the slope of its curve (trend) or by name; LEs without a value on a
page are named below it instead. Suppressed values are not reconstructed: such a period is a gap
in the curve, marked by a hollow circle below the baseline. A stacked bar shows the parts that
aren't suppressed; the rest up to its total is outlined if it consists of several suppressed
parts, and left out, with the hollow circle, if it is a single one.
The labels of the figures are those of rd_time_series.py, which has to be in the same directory.

Example:
  uv run plot_time_series.py time-series.csv --output plots     # plots.pdf, plots_<figure>.png
"""

import argparse
import csv
import math
import sys
from dataclasses import dataclass

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator
from matplotlib.transforms import offset_copy

import rd_time_series as ts


# ---------------------------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------------------------

PERCENT, DAYS, COUNT, SHARES, COUNTS = "percent", "days", "count", "shares", "counts"

BY_INITIAL, BY_SUBMISSION = "der Erstmeldung", "der Einreichung"


@dataclass
class Figure:
    name: str               # of the PNG
    title: str
    block: str
    item: str               # the item drawn, of stacked bars (SHARES, COUNTS) the one of their total
    kind: str
    note: str               # what the values are, or refer to; {base} is the Bezug of the item, {dates} see page()
    caveat: str = None      # what the reader has to know to read the curves
    parts: tuple = ()       # of stacked bars: ((label, items), ...)
    period: str = BY_INITIAL
    hidden_with: tuple = () # items whose suppression leaves the value out, see ts.Line.part
    needs: str = None       # drawn only if this item is counted at all
    unless: str = None      # drawn only if the CSV doesn't contain this item


# why no consent was transmitted, grouped for the stacked bars
REASONS = (
    ("vom Patienten abgelehnt",             ts.JUSTIFICATIONS[1:2]),
    ("anderer patientenbezogener Grund",    ts.JUSTIFICATIONS[0:1] + ts.JUSTIFICATIONS[2:4]),
    ("technisch oder organisatorisch (LE)", ts.JUSTIFICATIONS[4:6]),
    ("ohne Begründung, anderer Wert",       (ts.NO_JUSTIFICATION, ts.OTHER_JUSTIFICATION, ts.NOT_AN_OBJECT)),
)
REASONS = tuple((label, tuple(f"{ts.CONSENT_MISSING}: {r}" for r in reasons)) for label, reasons in REASONS)
REFUSED = f"{ts.CONSENT_MISSING}: {ts.REFUSAL}"
TYPES = tuple((label, (f"Einreichungen {kind}",)) for kind, label in (
    ("initial", "Erstmeldung (initial)"), ("followup", "Folgemeldung (followup)"),
    ("addition", "Ergänzung (addition)"), ("correction", "Korrektur (correction)"))) + (("anderer Typ", (ts.OTHER_TYPE,)),)

YOUNG_PERIODS = "Junge Zeiträume liegen zwangsläufig niedriger: ihre Follow-ups stehen noch aus."

FIGURES = (
    Figure("faelle", "Fälle", ts.B_CASES, ts.CASES, COUNT, "Episoden mit Erstmeldung"),
    Figure("consent_uebermittelt", "Broad Consent übermittelt", ts.B_CONSENT, ts.CONSENT_TRANSMITTED, PERCENT,
           "Anteil der Fälle"),
    Figure("consent_zustimmung", "Zustimmungsquote", ts.B_CONSENT, ts.CONSENT_RATE, PERCENT,
           "gültiger Broad Consent, Anteil der Fälle mit übermitteltem Broad Consent",
           "Obergrenze der Zustimmung: wer ohne unterschriebenes Formular ablehnt, fehlt im Nenner. "
           "Abgelaufene Consents zählen als nicht gültig."),
    Figure("consent_zustimmung_ablehnung", "Zustimmungsquote mit dokumentierter Ablehnung", ts.B_CONSENT,
           ts.CONSENT_RATE_REFUSALS, PERCENT,
           "gültiger Broad Consent, Anteil der Fälle mit Broad Consent oder dokumentierter Ablehnung",
           "Ablehnung: Begründung „patient refuses to sign consent“. Sie zählt nur, soweit sie übermittelt ist.",
           hidden_with=(REFUSED,), needs=REFUSED),
    Figure("consent_gueltig", "Broad Consent gültig", ts.B_CONSENT, ts.CONSENT_VALID, PERCENT, "Anteil der Fälle"),
    Figure("consent_fehlt", "Broad Consent nicht übermittelt, nach Begründung", ts.B_CONSENT, ts.CONSENT_MISSING, SHARES,
           "Anteil der Fälle", parts=REASONS),
    Figure("diagnose_alle", "Diagnoserate, alle Fälle", ts.B_STATUS, ts.rdm.DIAGNOSED, PERCENT, "Anteil der Fälle"),
    Figure("diagnose_abgeschlossen", "Diagnoserate, abgeschlossene Fälle", ts.B_RATE, ts.rdm.DIAGNOSED, PERCENT,
           "Anteil der abgeschlossenen Fälle (ohne offene)"),
    Figure("offen", "Offene Fälle", ts.B_STATUS, ts.OPEN, PERCENT,
           "undiagnostiziert und noch keine GFK2, Anteil der Fälle"),
    Figure("followup_inhalt", "Follow-up dokumentiert", ts.B_FOLLOW_UP, ts.GFK2_CONTENT, PERCENT,
           "followUp.followUpRds, Anteil der Fälle mit GFK2", YOUNG_PERIODS),
    Figure("followup_meldung", "Folgemeldung ab der GFK2-Meldung", ts.B_FOLLOW_UP, ts.GFK2_SUBMISSION, PERCENT,
           "Einreichung vom Typ followup, Anteil der Fälle mit GFK2", YOUNG_PERIODS),
    Figure("followup_faellig_inhalt", "Follow-up dokumentiert, fällige Fälle", ts.B_FOLLOW_UP, ts.DUE_CONTENT, PERCENT,
           "followUp.followUpRds, Anteil der {base}"),
    Figure("followup_faellig_meldung", "Folgemeldung ab der GFK2-Meldung, fällige Fälle", ts.B_FOLLOW_UP,
           ts.DUE_SUBMISSION, PERCENT, "Einreichung vom Typ followup, Anteil der {base}"),
    Figure("followup_faellig_frist", "Follow-up innerhalb der Frist, fällige Fälle", ts.B_FOLLOW_UP, ts.DUE_IN_TIME,
           PERCENT, "erstes Follow-up innerhalb der Frist, Anteil der {base}",
           "Zwischen den Zeiträumen vergleichbar: jeder Fall ist über die ganze Frist beobachtet."),
    Figure("followup_tage", "Tage von GFK2 bis zum ersten Follow-up", ts.B_FOLLOW_UP, ts.DAYS, DAYS,
           "Median, Band vom 1. bis zum 3. Quartil  ·  Ende: followUpDate{dates}, sonst Datum der Folgemeldung",
           "Zwischen den Zeiträumen nicht vergleichbar: junge Zeiträume enthalten erst die frühen Follow-ups, ihr Median "
           "fällt deshalb kürzer aus.", unless=ts.DUE_DAYS),
    Figure("followup_faellig_tage", "Tage von GFK2 bis zum ersten Follow-up innerhalb der Frist", ts.B_FOLLOW_UP,
           ts.DUE_DAYS, DAYS, "Median, Band vom 1. bis zum 3. Quartil  ·  Ende: followUpDate, sonst Datum der Folgemeldung",
           "Nur {base} und Follow-ups in dieser Frist nach der GFK2: zwischen den Zeiträumen vergleichbar."),
    Figure("einreichungen", "Einreichungen nach Art", ts.B_SUBMISSIONS, ts.SUBMISSIONS, COUNTS, "ohne Testeinreichungen",
           parts=TYPES, period=BY_SUBMISSION),
)

# colours: the series, the further parts of stacked bars in this order, and the chrome
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#8a63d2")
SURFACE, INK, MUTED, GRID, REFERENCE = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df", "#8c8b86"
PAGE = (11.69, 8.27)   # A4 landscape, inches
SMALL, NORMAL, LARGE = 9, 10, 14
BELOW = 11             # points below the baseline of a panel, for the marks of suppressed values


# ---------------------------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------------------------

class Hidden:
    """A suppressed value"""


HIDDEN = Hidden()


def number(text):
    if text == "":
        return None
    return HIDDEN if text.startswith("<") else float(text.replace(",", "."))


def load(path, delimiter):
    """(LEs, periods, lines, threshold): lines[(LE, block, item)][period], the CSV line with its numbers parsed"""
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f, delimiter=delimiter))
    if not rows or "Zeitraum" not in rows[0]:
        raise ValueError(f"{path}: not a CSV of rd_time_series.py (delimiter '{delimiter}'?)")
    les, periods, lines, threshold = [], [], {}, None
    for r in rows:
        if r["LE"] not in les:
            les.append(r["LE"])
        if r["Zeitraum"] not in periods:
            periods.append(r["Zeitraum"])
        for column in ("Wert", "Nenner", "Anteil_%", "Median", "Q1", "Q3", "n"):
            if r[column].startswith("<"):
                threshold = int(r[column][1:])
            r[column] = number(r[column])
        lines.setdefault((r["LE"], r["Block"], r["Kennzahl"]), {})[r["Zeitraum"]] = r
    periods = [p for p in periods if p != ts.WHOLE]
    if not periods:
        raise ValueError(f"{path}: no periods, nothing to plot")
    return les, periods, lines, threshold


# ---------------------------------------------------------------------------------------------
# Values: a number, HIDDEN or None (no cases)
# ---------------------------------------------------------------------------------------------

def count(lines, le, block, item, period):
    r = lines.get((le, block, item), {}).get(period)
    return r["Wert"] if r else None


def withheld(lines, le, block, item, period):
    """A count is suppressed, or left out with its suppressed base"""
    r = lines.get((le, block, item), {}).get(period)
    return r is not None and HIDDEN in (r["Wert"], r["Nenner"])


def value(figure, lines, le, period):
    """The value of an LE in a period"""
    r = lines.get((le, figure.block, figure.item), {}).get(period)
    if r is None:
        return None
    # rd_time_series.py leaves out what is counted within suppressed cases, or would disclose a suppressed part
    within = [(ts.B_CASES, ts.CASES)] if figure.period == BY_INITIAL and figure.item != ts.CASES else []
    within += [(figure.block, item) for item in figure.hidden_with]
    if any(withheld(lines, le, block, item, period) for block, item in within):
        return HIDDEN
    if figure.kind in (PERCENT, SHARES):
        return HIDDEN if HIDDEN in (r["Wert"], r["Nenner"]) else r["Anteil_%"]
    if figure.kind == DAYS:
        return HIDDEN if HIDDEN in (r["n"], r["Nenner"]) else r["Median"]
    return r["Wert"]


def bar(figure, lines, le, period):
    """
    The stacked bar of an LE in a period: (the heights of its parts, its total, the number of
    suppressed items). A part is as high as its items that aren't suppressed, shares as percentage
    of the cases; the total is a number, HIDDEN or None.
    """
    total = value(figure, lines, le, period)
    cases = count(lines, le, ts.B_CASES, ts.CASES, period) if figure.kind == SHARES else None
    scale = 100 / cases if shown(cases) and cases else 1
    heights, hidden = [], 0
    for _, items in figure.parts:
        counts = [count(lines, le, figure.block, item, period) for item in items]
        hidden += counts.count(HIDDEN)
        heights.append(scale * sum(c for c in counts if shown(c)))
    if figure.kind == SHARES and not (shown(cases) and cases):
        heights = [0] * len(heights)
    return heights, total, hidden


def rest(heights, total, hidden):
    """
    The height of the suppressed parts of a stacked bar together, drawn as an outline: 0 if a
    single item is suppressed, which the outline would disclose, or the total isn't shown
    """
    return max(total - sum(heights), 0) if shown(total) and hidden >= 2 else 0


def shown(v):
    return v is not None and v is not HIDDEN


def without_values(figure, lines, le, periods):
    """An LE has nothing to draw on a page: no value, of counts none but 0, and none suppressed"""
    values = [value(figure, lines, le, p) for p in periods]
    if figure.kind in (SHARES, COUNTS):
        values += [HIDDEN for p in periods if bar(figure, lines, le, p)[2]]
    nothing = (None, 0) if figure.kind in (COUNT, COUNTS) else (None,)
    return all(v in nothing for v in values)


def slope(values):
    """Least squares slope of the shown values over the periods, None with fewer than two"""
    points = [(x, v) for x, v in enumerate(values) if shown(v)]
    if len(points) < 2:
        return None
    mean_x, mean_y = sum(x for x, _ in points) / len(points), sum(y for _, y in points) / len(points)
    return sum((x - mean_x) * (y - mean_y) for x, y in points) / sum((x - mean_x) ** 2 for x, _ in points)


def text(v, kind):
    """A value as German text"""
    if v is HIDDEN:
        return "unterdrückt"
    if v is None:
        return "–"
    if kind in (COUNT, COUNTS):
        return f"n = {v:.0f}"
    if kind == DAYS:
        return f"{v:.1f}".replace(".", ",").replace(",0", "") + " Tage"
    return f"{v:.1f}".replace(".", ",") + " %"


def tick(period):
    """2026-01 -> 01/26, 2026-Q1 -> Q1/26"""
    year, part = period.split("-")
    return f"{part}/{year[2:]}"


def short(le):
    """The LE without the submitterIds of its label"""
    return le.split(" (")[0]


# ---------------------------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------------------------

def page(figure, les, periods, lines, threshold, order):
    """The page of a figure, None if the CSV doesn't contain it or has no value to show for it"""
    total_key = (ts.rdm.TOTAL, figure.block, figure.item)
    if total_key not in lines or (figure.unless and (ts.rdm.TOTAL, figure.block, figure.unless) in lines):
        return None
    if figure.needs and count(lines, ts.rdm.TOTAL, figure.block, figure.needs, ts.WHOLE) in (None, 0):
        return None

    stacked = figure.kind in (SHARES, COUNTS)
    with_total = figure.kind in (PERCENT, DAYS, SHARES)     # counts of the total are on another scale
    if figure.period == BY_INITIAL:
        # without the periods before the first and after the last initial submission
        cases = lines[(ts.rdm.TOTAL, ts.B_CASES, ts.CASES)]
        filled = [i for i, p in enumerate(periods) if cases[p]["Wert"]]
        periods = periods[filled[0]:filled[-1] + 1] if filled else periods
    candidates = [le for le in les if le != ts.rdm.TOTAL] or [ts.rdm.TOTAL]
    panels = [le for le in candidates if not without_values(figure, lines, le, periods + [ts.WHOLE])]
    left_out = [short(le) for le in candidates if le not in panels]
    series = {le: [value(figure, lines, le, p) for p in periods] for le in les}
    whole = {le: value(figure, lines, le, ts.WHOLE) for le in les}
    if not any(shown(v) for le in panels for v in series[le] + [whole[le]]):
        return None

    def rank(le):
        v = slope(series[le]) if order == "trend" else whole[le] if shown(whole[le]) else None
        return (v is None, -(v or 0), le)

    if order != "name":
        panels.sort(key=rank)

    bars = {le: [bar(figure, lines, le, p) for p in periods] for le in panels} if stacked else {}
    outlined = any(rest(*b) for le in panels for b in bars[le]) if stacked else False

    # legend: the marks of the LE, then the reference line and the marks of suppressed values; the
    # parts of stacked bars that occur get a row of their own
    if stacked:
        occurring = [any(b[0][i] for le in panels for b in bars[le]) for i in range(len(figure.parts))]
        marks = [Patch(facecolor=c, label=label) for c, (label, _), used in zip(SERIES, figure.parts, occurring) if used]
    elif figure.kind == COUNT:
        marks = [Patch(facecolor=SERIES[0], label="LE")]
    elif figure.kind == DAYS:
        marks = [Line2D([], [], color=SERIES[0], linewidth=2, marker="o", markersize=4, label="LE (Median)"),
                 Patch(facecolor=SERIES[0], alpha=0.18, label="LE (1. bis 3. Quartil)")]
    else:
        marks = [Line2D([], [], color=SERIES[0], linewidth=2, marker="o", markersize=4, label="LE")]
    others = [Line2D([], [], color=REFERENCE, linewidth=1, label=f"{ts.rdm.TOTAL} (alle LE)")] if with_total else []
    if outlined:
        others.append(Patch(facecolor="none", edgecolor=REFERENCE, linewidth=0.8, hatch="/////",
                            label="mehrere unterdrückte Teile zusammen"))
    few = f"1 bis {threshold - 1} Fälle" if threshold else "zu wenige Fälle"
    hidden_mark = Line2D([], [], linestyle="none", marker="o", markersize=4, markerfacecolor=SURFACE,
                         markeredgecolor=MUTED, markeredgewidth=0.9,
                         label=f"ein Teil unterdrückt und nicht gezeichnet ({few})" if stacked else f"unterdrückt ({few})")

    # header: title, what the values are, what to know about them; then the legend
    row = lines[total_key].get(ts.WHOLE) or {}
    base = row.get("Bezug") or ""
    unit = "Quartal" if periods and "Q" in periods[0] else "Monat"
    # how many of all durations end at a followUpDate, if the total tells
    of_date = lines.get((ts.rdm.TOTAL, figure.block, ts.DAYS_FROM_DATE), {}).get(ts.WHOLE)
    dates = (f" ({of_date['Wert']:.0f} von {of_date['Nenner']:.0f})"
             if of_date and shown(of_date["Wert"]) and shown(of_date["Nenner"]) and of_date["Nenner"] else "")
    subtitles = [f"{figure.note.format(base=base, dates=dates)}  ·  je LE und {unit} {figure.period}"]
    if figure.caveat:
        subtitles.append(figure.caveat.format(base=base))
    legend_rows = 2 if stacked else 1
    legend_top = 0.915 - 0.032 * (len(subtitles) - 1) - 0.015
    top_edge = legend_top - 0.035 * (legend_rows - 1) - 0.07

    columns = min(len(panels), math.ceil(math.sqrt(len(panels) * 1.3)))
    rows = math.ceil(len(panels) / columns)
    bottom_edge, gap = 0.14 if left_out else 0.115, 0.45    # room for the footnotes; gap between the rows in inches
    panel_height = ((top_edge - bottom_edge) * PAGE[1] - (rows - 1) * gap) / rows
    fig, axes = plt.subplots(rows, columns, figsize=PAGE, sharex=True, sharey=True, squeeze=False)
    fig.patch.set_facecolor(SURFACE)
    fig.subplots_adjust(left=0.06, right=0.985, top=top_edge, bottom=bottom_edge, hspace=gap / panel_height, wspace=0.1)

    # one scale for all panels; percentages up to 25, 50 or 100
    percent = figure.kind in (PERCENT, SHARES)
    drawn = [v for le in panels + [ts.rdm.TOTAL] * with_total for v in series[le] if shown(v)]
    drawn += [sum(b[0]) for le in panels for b in bars.get(le, [])]
    if figure.kind == DAYS:
        drawn += [r["Q3"] for le in panels for r in lines[(le, figure.block, figure.item)].values() if isinstance(r["Q3"], float)]
    highest = max(drawn, default=0)
    top = (25 if highest <= 22 else 50 if highest <= 47 else 100) if percent else highest * 1.08 or 1
    below = BELOW / 72 / panel_height                       # share of a panel below its baseline
    xs = list(range(len(periods)))
    labels = max(2, int(PAGE[0] * 0.9 / columns / 0.6))     # tick labels fitting below a panel
    step = math.ceil(len(periods) / labels)
    # centred, and off the edges of the panel, so that the labels of neighbouring panels stay apart
    offset = ((len(periods) - 1) % step) // 2 or step // 2
    hidden_anywhere = False

    for i, ax in enumerate(axes.flat):
        if i >= len(panels):
            ax.set_visible(False)
            continue
        le = panels[i]
        values = series[le]
        hidden = [x for x, v in zip(xs, values) if v is HIDDEN]
        ax.set_facecolor(SURFACE)
        ax.axhline(0, color=REFERENCE, linewidth=0.8, zorder=5)
        ax.grid(axis="y", color=GRID, linewidth=0.6)
        ax.set_axisbelow(True)
        for side in ax.spines.values():
            side.set_visible(False)
        ax.tick_params(length=0, labelsize=SMALL, colors=MUTED)

        if with_total:
            total = [v if shown(v) else math.nan for v in series[ts.rdm.TOTAL]]
            # the small markers show a value between gaps
            ax.plot(xs, total, color=REFERENCE, linewidth=1, marker="o", markersize=1.5, zorder=2 if not stacked else 4)
        if stacked:
            # a suppressed item is marked where the bar doesn't show it as part of the outlined rest
            hidden = [x for x, b in zip(xs, bars[le]) if b[1] is HIDDEN or (b[2] and not rest(*b))]
            bottom = [0] * len(xs)
            for part, colour in enumerate(SERIES[:len(figure.parts)]):
                heights = [b[0][part] for b in bars[le]]
                ax.bar(xs, heights, 0.72, bottom=bottom, color=colour, edgecolor=SURFACE, linewidth=0.6, zorder=3)
                bottom = [b + h for b, h in zip(bottom, heights)]
            rests = [(x, rest(*b), done) for x, b, done in zip(xs, bars[le], bottom) if rest(*b)]
            ax.bar([x for x, _, _ in rests], [r for _, r, _ in rests], 0.72, bottom=[done for _, _, done in rests],
                   facecolor="none", edgecolor=REFERENCE, linewidth=0.8, hatch="/////", zorder=3)
        elif figure.kind == COUNT:
            ax.bar(xs, [v if shown(v) else 0 for v in values], 0.72, color=SERIES[0], zorder=3)
        else:
            if figure.kind == DAYS:
                quartiles = [lines[(le, figure.block, figure.item)].get(p) for p in periods]
                low = [r["Q1"] if r and isinstance(r["Q1"], float) else math.nan for r in quartiles]
                high = [r["Q3"] if r and isinstance(r["Q3"], float) else math.nan for r in quartiles]
                ax.fill_between(xs, low, high, color=SERIES[0], alpha=0.18, linewidth=0, zorder=2)
                ax.vlines(xs, low, high, color=SERIES[0], alpha=0.35, linewidth=1.2, zorder=2)   # also a lone period
            ax.plot(xs, [v if shown(v) else math.nan for v in values], color=SERIES[0], linewidth=2,
                    marker="o", markersize=4, zorder=4)
        hidden_anywhere |= bool(hidden)
        ax.plot(hidden, [0] * len(hidden), linestyle="none", marker="o", markersize=4, markerfacecolor=SURFACE,
                markeredgecolor=MUTED, markeredgewidth=0.9, zorder=6, clip_on=False,
                transform=offset_copy(ax.transData, fig=fig, y=-BELOW / 2, units="points"))

        ax.set_title(short(le), loc="left", fontsize=NORMAL, fontweight="bold", color=INK, pad=4)
        ax.annotate(text(whole[le], figure.kind), (1, 1), xycoords="axes fraction", xytext=(0, 4),
                    textcoords="offset points", ha="right", va="baseline", fontsize=SMALL, color=MUTED)
        upper = top * (1.03 if percent else 1)
        ax.set_ylim(-upper * below / (1 - below), upper)
        ax.set_xlim(-0.6, len(periods) - 0.4)
        ax.set_xticks(xs[offset::step])
        ax.set_xticklabels([tick(p) for p in periods[offset::step]])
        if percent:
            ax.set_yticks({25: [0, 10, 20], 50: [0, 25, 50], 100: [0, 50, 100] if rows > 3 else [0, 25, 50, 75, 100]}[top])
        else:
            ax.yaxis.set_major_locator(MaxNLocator(4, integer=True, min_n_ticks=2))
            ax.set_yticks([t for t in ax.get_yticks() if 0 <= t <= top])
        ax.tick_params(labelbottom=i + columns >= len(panels))    # also above a gap in the last row

    orders = {"level": "nach dem Wert über den gesamten Zeitraum, absteigend",
              "trend": "nach der Steigung der Kurve, absteigend", "name": "nach dem Namen"}
    fig.text(0.06, 0.955, figure.title, fontsize=LARGE, fontweight="bold", color=INK, va="center")
    for n, subtitle in enumerate(subtitles):
        fig.text(0.06, 0.915 - 0.032 * n, subtitle, fontsize=NORMAL, color=MUTED, va="center")
    others += [hidden_mark] if hidden_anywhere else []
    for n, handles in enumerate((marks, others) if stacked else (marks + others,)):
        if handles:
            fig.add_artist(fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.052, legend_top - 0.035 * n),
                                      ncol=len(handles), frameon=False, fontsize=SMALL, labelcolor=INK,
                                      handlelength=1.6, columnspacing=1.8))
    footnotes = [f"Rechts über jedem Feld: Wert des LE über den gesamten Zeitraum. Reihenfolge der Felder: {orders[order]}.",
                 "Lücke: keine Fälle im Zeitraum oder Wert unterdrückt. Erster und letzter Zeitraum können unvollständig sein."]
    if left_out:
        named = ", ".join(left_out[:12]) + (f" und {len(left_out) - 12} weitere" if len(left_out) > 12 else "")
        footnotes.append(f"Ohne Wert auf dieser Seite und nicht dargestellt: {named}.")
    fig.text(0.06, 0.012, "\n".join(footnotes), fontsize=SMALL, color=MUTED, va="bottom", linespacing=1.5)
    return fig


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="CSV of rd_time_series.py")
    parser.add_argument("--order", choices=("level", "trend", "name"), default="level",
                        help="order of the panels (default: %(default)s)")
    parser.add_argument("--delimiter", default=";", help="CSV delimiter (default: %(default)s)")
    parser.add_argument("--dpi", type=int, default=150, help="resolution of the PNGs (default: %(default)s)")
    parser.add_argument("--output", default="rd-time-series", help="output path without extension (default: %(default)s)")
    args = parser.parse_args(argv)

    try:
        les, periods, lines, threshold = load(args.input, args.delimiter)
    except ValueError as error:
        parser.error(str(error))
    pages = [(figure, page(figure, les, periods, lines, threshold, args.order)) for figure in FIGURES]
    written = [(figure, fig) for figure, fig in pages if fig is not None]
    if not written:
        parser.error(f"{args.input}: no values, nothing to plot")
    with PdfPages(f"{args.output}.pdf") as pdf:
        for figure, fig in written:
            pdf.savefig(fig, facecolor=fig.get_facecolor())
            fig.savefig(f"{args.output}_{figure.name}.png", dpi=args.dpi, facecolor=fig.get_facecolor())
            plt.close(fig)
    left_out = [figure.name for figure, fig in pages if fig is None]
    if left_out:
        print(f"Not drawn, not in the CSV or without values: {', '.join(left_out)}", file=sys.stderr)
    print(f"Written: {args.output}.pdf, {', '.join(f'{args.output}_{figure.name}.png' for figure, _ in written)}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
