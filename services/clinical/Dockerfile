FROM golang:1.23-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY *.go ./
RUN go mod tidy && CGO_ENABLED=0 go build -o /out/clinical .
RUN CGO_ENABLED=0 go test ./...

FROM alpine:3.20
RUN apk add --no-cache ca-certificates tzdata
COPY --from=build /out/clinical /usr/local/bin/clinical
EXPOSE 8081
ENTRYPOINT ["/usr/local/bin/clinical"]
