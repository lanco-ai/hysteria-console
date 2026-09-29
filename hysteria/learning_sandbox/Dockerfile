FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
RUN pip install --no-cache-dir numpy==2.5.3 matplotlib==3.11.2
COPY runner.py /runner.py
USER 65534:65534
WORKDIR /workspace
ENV HOME=/tmp MPLCONFIGDIR=/tmp/matplotlib MPLBACKEND=Agg OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
