FROM amazoncorretto:21

COPY ./dnpm-ccdn-evaluation.jar ./version.txt /opt/
# Extracted onto the host by "extract-scripts", see entrypoint.sh
COPY ./scripts /opt/scripts
COPY --chmod=755 entrypoint.sh /

LABEL org.opencontainers.image.licenses=MIT
LABEL org.opencontainers.image.source=https://github.com/dnpm-dip/cdn-evaluation-module
LABEL org.opencontainers.image.description="DNPM - Statistical evaluations on the backups of the Central Data Node"

ENTRYPOINT ["/entrypoint.sh"]
CMD ["help"]
