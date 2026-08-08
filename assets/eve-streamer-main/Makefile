.PHONY: proto build run clean bpf

# generate go code from protobuf definitions
fb:
	@which flatc >/dev/null || (echo "flatc not installed" && exit 1)
	flatc --go --grpc -o fb fb/flow.fbs

proto:
	@which protoc >/dev/null || (echo "protoc not installed" && exit 1)
	# generate Go code using source_relative so files land next to the proto
	protoc \
		--go_out=pb --go_opt=paths=source_relative \
		--go-grpc_out=pb --go-grpc_opt=paths=source_relative \
		pb/event.proto

bpf:
	@which clang >/dev/null || (echo "clang not installed" && exit 1)
	clang -target bpf -O2 -c bpf_capture.c -o bpf_capture.o

build: proto bpf
	go mod download
	# build the main package as a single binary
	go build -o bin/eve-streamer .

run: build
	./bin/eve-streamer

clean:
	rm -f bin/eve-streamer
	rm -f bpf_capture.o
	rm -rf pb/*.pb.go

docker-build:
	docker build -t eve-streamer .

.DEFAULT_GOAL := build
