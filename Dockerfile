FROM node:22-alpine AS builder

WORKDIR /build
COPY web/package.json .
COPY web/package-lock.json .
RUN npm ci --legacy-peer-deps
COPY ./web .
COPY ./VERSION .
RUN DISABLE_ESLINT_PLUGIN='true' VITE_REACT_APP_VERSION=$(cat VERSION) npm run build

FROM golang:alpine AS builder2
ENV GO111MODULE=on CGO_ENABLED=0

ARG TARGETOS
ARG TARGETARCH
ENV GOOS=${TARGETOS:-linux} GOARCH=${TARGETARCH:-amd64}
ENV GOEXPERIMENT=greenteagc

WORKDIR /build

ADD go.mod go.sum ./
RUN go mod download

COPY . .
COPY --from=builder /build/dist ./web/dist
RUN go build -ldflags "-s -w -X 'github.com/QuantumNous/new-api/common.Version=$(cat VERSION)'" -o new-api

FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates tzdata wget python3 python3-pip python-is-python3 \
    && rm -rf /var/lib/apt/lists/* \
    && update-ca-certificates \
    && npm install -g @openai/codex

ENV PYTHONUNBUFFERED=1
ENV CHATCORE_INTERNAL_CHAT_HOST=127.0.0.1
ENV CHATCORE_INTERNAL_CHAT_PORT=1455

WORKDIR /app
COPY --from=builder2 /build/new-api /new-api
COPY --from=builder2 /build/embedded-chatmock /app/embedded-chatmock
COPY scripts/start-single-service.sh /start-single-service.sh
RUN python -m pip install --no-cache-dir --break-system-packages --upgrade pip \
    && python -m pip install --no-cache-dir --break-system-packages -r /app/embedded-chatmock/requirements.txt \
    && chmod +x /start-single-service.sh

EXPOSE 3000
WORKDIR /data
ENTRYPOINT ["/start-single-service.sh"]
