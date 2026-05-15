FROM golang:1.23-alpine AS build

WORKDIR /src
RUN apk add --no-cache ca-certificates && update-ca-certificates
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o /out/privacy-shield ./cmd/privacy-shield

FROM alpine:3.21

RUN adduser -D -H appuser
USER appuser
COPY --from=build /out/privacy-shield /usr/local/bin/privacy-shield
EXPOSE 8080
ENTRYPOINT ["privacy-shield"]

