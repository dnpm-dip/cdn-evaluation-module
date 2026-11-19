#!/bin/bash

ScriptsSource=/opt/scripts
DefaultScriptsTarget=/scripts

# Copies the Python scripts shipped in the image into a directory mounted from the host, to run
# them there manually. Existing files of the same name are overwritten, others (e.g. results) kept.
# The copies get the owner of the target directory, rather than root.
extract_scripts() {
  local target="${1:-$DefaultScriptsTarget}"
  target="${target%/}"

  if [ ! -d "$target" ]; then
    echo "Error: $target doesn't exist; mount a host directory there, e.g. -v \"\$PWD/scripts:$target\"" >&2
    exit 1
  fi
  # Without a mount, the copies would vanish with the container
  if ! awk -v t="$target" '$5 == t { found = 1 } END { exit !found }' /proc/self/mountinfo; then
    echo "Error: $target is no mounted host directory, mount one, e.g. -v \"\$PWD/scripts:$target\"" >&2
    exit 1
  fi

  local owner
  owner=$(stat -c %u:%g "$target") || exit 1

  shopt -s globstar dotglob nullglob
  cd "$ScriptsSource" || exit 1
  echo "Extracting the scripts of version $(cat /opt/version.txt) into $target (owner $owner):"
  for path in **; do
    if [ -d "$path" ]; then
      mkdir -p "$target/$path" && chown "$owner" "$target/$path" || exit 1
    else
      cp "$path" "$target/$path" && chown "$owner" "$target/$path" || exit 1
      echo "  $path"
    fi
  done
  cp /opt/version.txt "$target/VERSION.txt" && chown "$owner" "$target/VERSION.txt" || exit 1
}

case "$1" in
  extract-scripts)
    shift
    extract_scripts "$@"
    ;;
  *)
    # JAVA_OPTS e.g. for more heap (-Xmx4g) with large submissions, or -Dlogback.configurationFile=...
    exec java $JAVA_OPTS -jar /opt/dnpm-ccdn-evaluation.jar "$@"
    ;;
esac
