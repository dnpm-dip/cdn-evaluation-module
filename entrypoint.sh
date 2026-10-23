#!/bin/bash

# JAVA_OPTS e.g. for more heap (-Xmx4g) with large submissions, or -Dlogback.configurationFile=...
exec java $JAVA_OPTS -jar /opt/dnpm-ccdn-evaluation.jar "$@"
