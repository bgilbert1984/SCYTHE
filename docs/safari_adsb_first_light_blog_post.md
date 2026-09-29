# First Light: Live Aircraft on the Safari Pipeline

**Date:** September 28, 2026
**Author:** SCYTHE Core Engineering Team
**Category:** Public Signal Safari, ADS-B, Live Capture

---

Tonight the Public Signal Safari stopped being a prototype and started being a window.

## From synthetic truth to live sky

The ADS-B pipeline began the evening as a verified synthetic: known-truth 1090ES messages generated in software, decoded bit-identical through a numpy preamble matched filter and pyModeS, and served through a replay server that mirrors the AIS ingest path — REST tracks, status, Socket.IO updates every two seconds, snapshot on connect. That was the scaffolding, and it was verified end to end before anything real touched it.

Then we pointed our own antenna at the real sky. A bounded 1090 MHz capture off the NESDR, decoded live, sanity-gated against Seattle airspace, and dropped into the serving path. No pipeline changes were required for the swap, because the pipeline was built for exactly this moment: the replay loop reads a tracks file, and the file went from synthetic to live.

The first live decode wrote its own headline. Forty-four valid messages, four aircraft — and among them QXE2009, a Horizon Air regional at 15,975 feet over 47.88 N, 122.27 W. Directly above Paine Field, minutes from the antenna. Our own sky, on our own globe.

## It runs itself now

The pipeline is no longer a demo you launch; it is infrastructure. Every twenty minutes scythe-1 stops its stream, captures three minutes at 1090 MHz, decodes with a streaming reader (the 7.7 GB VM taught us that the whole-file decoder does not survive contact with a real capture), sanity-checks the tracks, and publishes — then restores the stream and verifies full rate before standing down. Every five minutes dedirock pulls the result. The API server serves it all; the `--safari` gate holds the public instance read-only. Between refreshes, the dongle stays available for the §5.21 catalogue run. Nothing about the demo costs the measurement program a minute it was promised.

## The build paid for itself

Integration work earns its keep when it finds something. This one did: the installed python-socketio 5.17 removed the `broadcast` keyword argument, which meant the existing AIS `ais_update` emit had been raising a `TypeError` on every broadcast — silently, into the void, for who knows how long. Both the AIS and the new ADS-B paths now emit cleanly. The detector was more honest than the label, again. That is becoming a theme around here.

## The evening around it

The Safari work sat inside a larger program: running Hugging Face raw-IQ datasets through the measurement machinery under strict quarantine — external captures as test vectors and content, never as evidence. The headline from that program: a published 96.9%-accurate signal classifier's "noise" class turned out to contain its own receiver's harmonic comb, teeth exactly 149,150 Hz apart in 100 of 100 noise captures and none of the 700 others. A fingerprinting mirror then showed the model wasn't even riding the comb — it had learned the statistical noise profile, and the comb was incidental. The detector saw the receiver; the classifier saw the noise. Both were right about their own question. That distinction — what a thing is evidence *of* — is the entire program.

What's next: the in-browser verification of the aircraft rendering, and then the public instance. The sky is already flowing. Soon it will have an audience.
