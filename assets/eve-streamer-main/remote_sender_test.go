package main

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"

	pb "github.com/yourorg/eve-streamer/pb"
)

type fakeRemoteConnection struct{}

func (fakeRemoteConnection) Close() error { return nil }

type fakeBatchStream struct {
	send func(*pb.EventBatch) error
}

func (s *fakeBatchStream) Send(batch *pb.EventBatch) error {
	return s.send(batch)
}

func (s *fakeBatchStream) CloseAndRecv() (*pb.EventAck, error) {
	return &pb.EventAck{Status: "completed"}, nil
}

func TestResilientRemoteSenderReconnectsAndRetriesSameBatch(t *testing.T) {
	batch := &pb.EventBatch{Events: []*pb.Event{{EventId: "stable-event-id"}}}
	var mu sync.Mutex
	connects := 0
	var received *pb.EventBatch

	sender := newResilientRemoteSender("receiver.test:50051")
	sender.retryInitial = time.Millisecond
	sender.retryMaximum = 2 * time.Millisecond
	sender.connector = func(context.Context, string) (remoteConnection, eventBatchStream, error) {
		mu.Lock()
		defer mu.Unlock()
		connects++
		if connects == 1 {
			return fakeRemoteConnection{}, &fakeBatchStream{send: func(*pb.EventBatch) error {
				return errors.New("receiver generation stopped")
			}}, nil
		}
		return fakeRemoteConnection{}, &fakeBatchStream{send: func(got *pb.EventBatch) error {
			received = got
			return nil
		}}, nil
	}

	if err := sender.Send(context.Background(), batch); err != nil {
		t.Fatalf("Send() error = %v", err)
	}
	if connects != 2 {
		t.Fatalf("connector calls = %d, want 2", connects)
	}
	if received != batch {
		t.Fatal("reconnected stream did not receive the original batch")
	}
}

func TestResilientRemoteSenderStopsRetryingWhenCancelled(t *testing.T) {
	sender := newResilientRemoteSender("receiver.test:50051")
	sender.retryInitial = time.Millisecond
	sender.retryMaximum = 2 * time.Millisecond
	sender.connector = func(context.Context, string) (remoteConnection, eventBatchStream, error) {
		return nil, nil, errors.New("receiver unavailable")
	}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Millisecond)
	defer cancel()
	err := sender.Send(ctx, &pb.EventBatch{Events: []*pb.Event{{EventId: "pending"}}})
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("Send() error = %v, want context deadline", err)
	}
}
