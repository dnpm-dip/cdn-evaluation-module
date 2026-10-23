FROM amazoncorretto:21

COPY ./dnpm-ccdn-evaluation.jar /opt/
COPY --chmod=755 entrypoint.sh /

LABEL org.opencontainers.image.licenses=MIT
LABEL org.opencontainers.image.source=https://github.com/dnpm-dip/cdn-evaluation-module
LABEL org.opencontainers.image.description="DNPM - Statistical evaluations on the backups of the Central Data Node"

ENTRYPOINT ["/entrypoint.sh"]
CMD ["help"]
