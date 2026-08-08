package main

import (
	"context"
	"fmt"
	"io"
	"log"
	"sync"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	pb "github.com/yourorg/eve-streamer/pb"
)

const (
	remoteRetryInitial = 100 * time.Millisecond
	remoteRetryMaximum = 5 * time.Second
)

type remoteConnection interface {
	Close() error
}

type eventBatchStream interface {
	Send(*pb.EventBatch) error
	CloseAndRecv() (*pb.EventAck, error)
}

type remoteConnector func(context.Context, string) (remoteConnection, eventBatchStream, error)

// resilientRemoteSender owns the long-lived client stream used by shipper mode.
// Send calls are serialized so reconnecting can atomically discard a poisoned
// stream and retry the same immutable batch on a new one.
type resilientRemoteSender struct {
	addr         string
	connector    remoteConnector
	retryInitial time.Duration
	retryMaximum time.Duration

	mu     sync.Mutex
	conn   remoteConnection
	stream eventBatchStream
}

func newResilientRemoteSender(addr string) *resilientRemoteSender {
	return &resilientRemoteSender{
		addr:         addr,
		connector:    dialRemoteStream,
		retryInitial: remoteRetryInitial,
		retryMaximum: remoteRetryMaximum,
	}
}

func dialRemoteStream(ctx context.Context, addr string) (remoteConnection, eventBatchStream, error) {
	opts := []grpc.DialOption{
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithStreamInterceptor(authStreamInterceptor),
	}
	conn, err := grpc.DialContext(ctx, addr, opts...)
	if err != nil {
		return nil, nil, err
	}
	stream, err := pb.NewEventStreamerClient(conn).StreamEvents(ctx)
	if err != nil {
		conn.Close()
		return nil, nil, err
	}
	return conn, stream, nil
}

func (s *resilientRemoteSender) connect(ctx context.Context) error {
	conn, stream, err := s.connector(ctx, s.addr)
	if err != nil {
		return err
	}
	s.conn = conn
	s.stream = stream
	return nil
}

func (s *resilientRemoteSender) reset() {
	if s.conn != nil {
		_ = s.conn.Close()
	}
	s.conn = nil
	s.stream = nil
}

func (s *resilientRemoteSender) waitForRetry(ctx context.Context, delay time.Duration) error {
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

// Send keeps a batch pending until it is accepted by a live gRPC stream or the
// caller cancels the context. This prevents a receiver restart from turning one
// stale stream into an unbounded sequence of dropped batches. The receiver's
// stable event IDs make replay of an ambiguous transport failure idempotent at
// the GraphOps ingestion boundary.
func (s *resilientRemoteSender) Send(ctx context.Context, batch *pb.EventBatch) error {
	if batch == nil || len(batch.Events) == 0 {
		return nil
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	delay := s.retryInitial
	attempt := 0
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		attempt++

		if s.stream == nil {
			if err := s.connect(ctx); err != nil {
				log.Printf("remote reconnect attempt %d failed: %v", attempt, err)
				if err := s.waitForRetry(ctx, delay); err != nil {
					return err
				}
				delay = min(delay*2, s.retryMaximum)
				continue
			}
		}

		if err := s.stream.Send(batch); err == nil {
			if attempt > 1 {
				log.Printf("remote stream recovered after %d attempts", attempt)
			}
			return nil
		} else {
			log.Printf("remote send attempt %d failed; reconnecting: %v", attempt, err)
			s.reset()
		}

		if err := s.waitForRetry(ctx, delay); err != nil {
			return err
		}
		delay = min(delay*2, s.retryMaximum)
	}
}

func (s *resilientRemoteSender) Close() error {
	s.mu.Lock()
	defer s.mu.Unlock()

	var closeErr error
	if s.stream != nil {
		if ack, err := s.stream.CloseAndRecv(); err != nil {
			closeErr = err
		} else {
			log.Printf("final ack from remote: count=%d status=%s", ack.Count, ack.Status)
		}
	}
	if s.conn != nil {
		if err := s.conn.Close(); err != nil && closeErr == nil {
			closeErr = err
		}
	}
	s.conn = nil
	s.stream = nil
	if closeErr != nil {
		return fmt.Errorf("close remote stream: %w", closeErr)
	}
	return nil
}

var _ io.Closer = (*resilientRemoteSender)(nil)
