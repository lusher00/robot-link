# Robot Link Protocol Design

**Version 1.0 design baseline**  
Raspberry Pi accessory computer and BeagleBone Blue machine controller

**Status** Approved architecture for implementation planning  
Date 19 September 2026

# Purpose and controlling decision

This document defines the complete baseline for a reusable, bidirectional packet link between a Raspberry Pi and a BeagleBone Blue over the USB RNDIS network. The BeagleBone is the autonomous machine controller. The Raspberry Pi is an optional high level accessory that may provide user interface, speech, perception, mapping, logging, and other services when available. Loss, reboot, or absence of the Pi must not prevent the BeagleBone from keeping the machine safe.

The protocol is ESPin32 packet protocol version 2 generalized for Linux, TCP, UART, and future transports. It preserves the existing byte oriented framing, CRC 16 XMODEM, state machine parser, ACK and NACK behavior, and command namespaces while adding protocol versioning, 16 bit message identifiers, 16 bit payload length, flags, and sequence numbers.

# Document authority and feature retention

This specification is the feature baseline. A later implementation may add compatible features, but it must not silently remove, narrow, or replace a requirement in this document. Any intentional change requires a new document revision, a short rationale, and an entry in the change log. The requirements matrix near the end provides the acceptance checklist.

# System roles

| **Component**                   | **Role**                                               | **Required behavior**                                                                                                                                      |
|---------------------------------|--------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------|
| BeagleBone Blue                 | Machine controller and TCP server                      | Controls balancing, motors, immediate safety, low level sensors, and machine state without depending on the Pi.                                            |
| Raspberry Pi robot link service | Optional accessory gateway and reconnecting TCP client | Runs beside hailo-tracker and other Pi applications. It owns the single Bone connection and exposes a local client API.                                    |
| hailo-tracker                   | Pi perception service                                  | Publishes tracked targets, detection events, camera health, and optional snapshots through the local robot link API. It remains independently restartable. |
| RNDIS link                      | Private IP network over USB                            | Carries one persistent TCP connection. The packet protocol does not depend on RNDIS and can use another byte stream transport.                             |
| ESPin32 devices                 | Existing embedded peers                                | Continue using compatible framing concepts. A compatibility adapter may translate legacy version 1 packets to version 2 messages.                          |

# Design principles

- Transport independence. Framing, validation, request matching, and dispatch do not call TCP, UART, BLE, or USB APIs directly.

- Machine safety remains local. Network messages never replace BeagleBone watchdogs, limits, e stop handling, or balance control.

- Explicit compatibility. Receivers validate versions, lengths, flags, and message identifiers and return defined errors where possible.

- Incremental extension. New message types and optional fields can be added without changing the framing layer.

- Observable failure. Disconnects, malformed frames, CRC errors, timeouts, queue overflow, and unsupported requests produce counters and logs.

- Bounded resources. Payloads, queues, parser work, and retry behavior have configured limits.

# Architecture

Raspberry Pi USB RNDIS BeagleBone Blue  
hailo-tracker --\\ balance control  
UI speech lidar -- local IPC --\> robot-linkd == TCP :5555 ==\> robot_link server  
logging mapping --/ motors sensors safety

The BeagleBone listens on a configurable address and TCP port, with 5555 as the default. The Pi continually attempts to connect using bounded backoff. Either peer may transmit packets at any time after session negotiation completes. TCP supplies ordered reliable bytes; the packet layer supplies framing, validation, message identity, correlation, application acknowledgements, compatibility checks, and diagnostics.

## Software layers

| **Layer**        | **Responsibilities**                                                                | **Must not own**                    |
|------------------|-------------------------------------------------------------------------------------|-------------------------------------|
| Application      | Robot state, motion requests, sensor data, power, speech, UI, logs, configuration   | Socket framing and byte parsing     |
| Message service  | Dispatch, subscriptions, request response matching, ACK NACK, timeouts, permissions | Transport specific reads and writes |
| Packet protocol  | Build, parse, CRC, length and version validation, resynchronization                 | Robot behavior and socket lifecycle |
| Transport        | Connect, accept, read, write all bytes, reconnect, socket options                   | Message interpretation              |
| Operating system | TCP IP, RNDIS interface, scheduling and service supervision                         | Protocol policy                     |

## Process model

On the Pi, robot-linkd is a standalone systemd service installed beside hailo-tracker. It owns the one persistent TCP session to the Bone and offers a versioned local Unix domain socket API to hailo-tracker, the web UI, speech, lidar, mapping, and future applications. This prevents independent applications from opening competing Bone sessions and lets any one Pi service restart without resetting the robot link. A Python client package hides the local IPC details.

On the Bone, the protocol library runs in a dedicated link thread or event loop and integrates with balance_bot through bounded application queues. Blocking network activity cannot stall the machine control loop. Application callbacks execute outside the parser critical section. Long work is handed to application queues.

## Pi service integration

| **Service**              | **Local interface**                                   | **Robot link responsibility**                                                                          |
|--------------------------|-------------------------------------------------------|--------------------------------------------------------------------------------------------------------|
| robot-linkd              | Unix domain socket at /run/robot-link/robot-link.sock | TCP lifecycle, session negotiation, subscriptions, permissions, correlation, queues, and diagnostics   |
| hailo-tracker            | Python robot_link client                              | Publish target tracks and events; subscribe to tracker configuration or snapshot requests when enabled |
| Web and operator UI      | Python or Node robot_link client                      | Subscribe to robot status and send permitted operator requests                                         |
| Speech and audio         | Python robot_link client                              | Consume SPEAK and PLAY_SOUND; publish completion or failure                                            |
| Future lidar and mapping | Language appropriate client                           | Publish derived pose, obstacle, or map summaries without owning machine control                        |

The local API uses the same message identifiers and typed payload definitions as the network protocol, but it does not need to expose raw TCP frames. robot-linkd assigns network sequences, enforces client permissions, tracks subscriptions, and identifies each local client in diagnostics.

## ROS 2 and SLAM boundary

ROS 2 is an optional Pi side subsystem and is recommended for lidar integration, coordinate transforms, sensor fusion, SLAM, visualization, and later navigation. ROS 2 does not run on the BeagleBone and does not replace robot-linkd. A separate robot-link-ros2 bridge converts selected robot link messages to ROS topics and converts permitted ROS commands back to expiring high level requests.

| **ROS function** | **Pi side input or output**                                          | **Robot link mapping**                                                                                 |
|------------------|----------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------|
| Lidar driver     | Publishes sensor_msgs LaserScan on /scan                             | No raw scan forwarding to the Bone by default                                                          |
| Wheel odometry   | Publishes nav_msgs Odometry on /wheel/odom                           | Bridge builds it from Bone encoder and motion state messages                                           |
| IMU              | Publishes sensor_msgs Imu on /imu/data                               | Bridge builds it from timestamped Bone IMU messages                                                    |
| Transforms       | Publishes base_link, laser, camera, odom, and map transforms         | Static geometry stays in ROS configuration; dynamic transforms use odometry and SLAM                   |
| SLAM             | Consumes scan, odometry, IMU, and transforms; publishes map and pose | Map remains on the Pi. A pose summary may be sent to the Bone if a machine feature needs it            |
| Navigation       | Produces a desired Twist or constrained equivalent                   | Bridge converts it to an expiring DRIVE_COMMAND under Bone limits and policy                           |
| Hailo perception | Publishes vision or custom detection topics                          | An adapter may publish tracker output to both robot-linkd and ROS without blocking the camera pipeline |

Use ROS 2 Jazzy on 64 bit Ubuntu 24.04 as the conservative starting point for the Pi. Begin with the lidar driver, robot state publisher, transforms, odometry, IMU, and slam_toolbox. Add Nav2 only after lidar timing, transforms, odometry, and repeatable mapping are stable. ROS processes may fail or restart without affecting balance control or the core Pi to Bone link.

# Connection ownership and lifecycle

1.  The BeagleBone starts its listener independently of the Pi and continues machine operation if no client exists.

2.  The Pi resolves the configured RNDIS address and opens a TCP connection to port 5555.

3.  Both peers exchange HELLO messages containing protocol range, role, implementation version, capability bits, boot identifier, and configured maximum payload.

4.  Each peer verifies that the protocol ranges overlap. The session uses the highest mutually supported version.

5.  The peers exchange READY after capability negotiation. Application messages are accepted only after both READY messages.

6.  Heartbeats run while the connection is idle or active. Link health uses received traffic and heartbeat deadlines, not TCP connection state alone.

7.  On EOF, socket error, heartbeat timeout, protocol violation requiring closure, or peer restart, pending requests fail with a link error and session state is discarded.

8.  The Pi reconnects with jittered exponential backoff. The BeagleBone returns to accept without interrupting machine control.

## Suggested lifecycle defaults

| **Parameter**      | **Default**    | **Constraint**                                           |
|--------------------|----------------|----------------------------------------------------------|
| Listen port        | 5555           | Configurable                                             |
| Connect timeout    | 3 s            | Does not block application startup                       |
| Heartbeat interval | 1 s            | Sent when no other traffic satisfies liveness            |
| Link timeout       | 3.5 s          | At least three heartbeat opportunities                   |
| Reconnect delay    | 0.25 s to 10 s | Exponential with jitter and reset after a stable session |
| Request timeout    | 1 s            | Message specific override allowed                        |
| Maximum retries    | 0 by default   | Retry only messages explicitly marked idempotent         |
| Maximum payload    | 4096 bytes     | Negotiated downward; compile time hard ceiling required  |

# Wire protocol

All multibyte integers use network byte order, most significant byte first. The CRC covers every byte from Version through the final payload byte. Start and end delimiters are excluded. The length field contains payload bytes only. Empty payloads are valid.

| **Offset** | **Size** | **Field**      | **Definition**                                                 |
|------------|----------|----------------|----------------------------------------------------------------|
| 0          | 1        | Start          | 0xAA                                                           |
| 1          | 1        | Version        | Wire protocol version; version 2 is 0x02                       |
| 2          | 2        | Payload length | Unsigned payload byte count                                    |
| 4          | 2        | Message type   | Namespace and command identifier                               |
| 6          | 1        | Flags          | Request, response, event, ACK policy, error, and reserved bits |
| 7          | 2        | Sequence       | Nonzero correlation identifier when required                   |
| 9          | N        | Payload        | Message specific encoding                                      |
| 9 + N      | 2        | CRC 16         | CRC 16 XMODEM over bytes 1 through 8 + N                       |
| 11 + N     | 1        | End            | 0x55                                                           |

AA 02 00 00 00 01 02 12 34 CRC_H CRC_L 55  
\| \| \| \| \| \| \| \|  
SOF VER LEN TYPE FLAGS SEQ CRC EOF

## Packet size rules

- The minimum frame is 12 bytes and carries a zero byte payload.

- The receiver rejects a payload length above its negotiated maximum before allocating or copying payload storage.

- The protocol supports a 16 bit length field, but implementations must use a smaller bounded operational maximum. Version 2 defaults to 4096 bytes.

- Large data such as maps, images, firmware, or files must use an explicit chunked transfer message family or a separate service. It must not bypass the negotiated limit.

## Flags

| **Bit** | **Name**      | **Meaning**                                                                                                 |
|---------|---------------|-------------------------------------------------------------------------------------------------------------|
| 0       | ACK REQUIRED  | Receiver must return ACK or NACK after validating and accepting or rejecting the message.                   |
| 1       | RESPONSE      | Message answers a prior request and uses the same sequence number.                                          |
| 2       | ERROR         | Response reports failure; payload contains a structured error code and optional detail.                     |
| 3       | EVENT         | Unsolicited state or telemetry event. It is not a request.                                                  |
| 4       | IDEMPOTENT    | Sender declares that replay with the same sequence and session is safe.                                     |
| 5       | HIGH PRIORITY | Queue ahead of normal traffic but never ahead of local safety processing.                                   |
| 6 to 7  | RESERVED      | Transmit zero. A version 2 receiver rejects nonzero reserved bits with UNSUPPORTED_FLAGS when it can reply. |

## Sequence number rules

- Sequence zero means no correlation. It is allowed for unacknowledged events and periodic telemetry.

- Requests, ACK REQUIRED messages, and responses use sequence values 1 through 65535.

- A response, ACK, or NACK copies the initiating sequence number.

- Sequence values are unique among outstanding requests in a session. Wrap is allowed only after the value is no longer outstanding.

- Sequence scope ends when the TCP session ends. HELLO includes a boot identifier so logs can distinguish peer restarts.

- Duplicate idempotent requests may return the cached outcome. Duplicate non idempotent requests are rejected with DUPLICATE_SEQUENCE.

## CRC algorithm

Use CRC 16 XMODEM with polynomial 0x1021, initial value 0x0000, no input or output reflection, and final XOR 0x0000. Transmit the high byte first. This matches the existing ESPin32 protocol algorithm. Include known answer vectors in every language implementation.

# Parser behavior

The parser accepts arbitrary byte chunks and processes bytes incrementally. A TCP read may contain part of one frame, one frame, or several frames. The parser must never assume a one to one relationship between recv calls and packets.

| **State**    | **Action**                           | **Failure behavior**                                            |
|--------------|--------------------------------------|-----------------------------------------------------------------|
| WAIT START   | Discard bytes until 0xAA             | Count discarded bytes only for diagnostics                      |
| READ HEADER  | Read Version through Sequence        | Reject unsupported version, reserved flags, or excessive length |
| READ PAYLOAD | Read exactly Payload length bytes    | Bound writes to the negotiated maximum                          |
| READ CRC     | Read two CRC bytes                   | Compare after full protected range is available                 |
| READ END     | Require 0x55                         | On mismatch, record framing error and resynchronize             |
| COMPLETE     | Publish immutable packet to dispatch | Reset parser before application callback                        |

Resynchronization must make forward progress on corrupt input. After an error, reset to WAIT START. If the byte that exposed the error is 0xAA, it may immediately become the next start byte. A malformed length must not cause the parser to wait for an unbounded stream. Repeated errors above a configurable threshold may close the session.

# Payload encoding

Version 2 uses explicitly encoded fields rather than transmitting native C structs. Native structs are forbidden on the wire because padding, alignment, endianness, compiler options, and floating point assumptions can differ. Each message definition states field order, width, signedness, units, scale, and optional tail fields.

- Unsigned and signed integers use fixed widths and network byte order.

- Boolean values use one byte, 0 for false and 1 for true.

- IEEE 754 binary32 may be used when a floating point value is appropriate; its four bytes are sent in network byte order.

- Strings are UTF 8 and use a 16 bit byte length followed by bytes. They are not NUL terminated on the wire.

- Arrays use an element count followed by encoded elements unless the message definition fixes the count.

- Units appear in field names or definitions. Preferred SI units are radians, radians per second, metres, seconds, volts, amperes, and degrees Celsius.

- Receivers accept a payload longer than the fields they understand only when the message definition permits append only evolution. They reject a payload shorter than the required prefix.

# Acknowledgements requests and errors

Transport delivery is not application acceptance. ACK means that the receiver validated and accepted a message for processing. It does not necessarily mean that a physical action completed. Commands that need completion confirmation use a typed response or later state event.

| **Message**    | **Type**                   | **Use**                                                                                    |
|----------------|----------------------------|--------------------------------------------------------------------------------------------|
| ACK            | 0x0004                     | Carries the acknowledged type and sequence. Optional status can report accepted or queued. |
| NACK           | 0x0005                     | Carries the rejected type, sequence, error code, and bounded diagnostic text.              |
| Typed response | Request specific           | Returns requested data or the result of an operation. RESPONSE is set.                     |
| ERROR response | Request specific or 0x0006 | Returns a structured failure. RESPONSE and ERROR are set.                                  |

## Standard error codes

| **Code** | **Name**            | **Meaning**                                         |
|----------|---------------------|-----------------------------------------------------|
| 0        | OK                  | No error                                            |
| 1        | MALFORMED           | Payload or frame fields are invalid                 |
| 2        | BAD_CRC             | CRC does not match                                  |
| 3        | UNSUPPORTED_VERSION | No compatible protocol version                      |
| 4        | UNSUPPORTED_TYPE    | Message identifier is unknown                       |
| 5        | UNSUPPORTED_FLAGS   | A reserved or invalid flag combination was used     |
| 6        | LENGTH              | Length is invalid or exceeds the limit              |
| 7        | NOT_READY           | Session negotiation is incomplete                   |
| 8        | BUSY                | Bounded resource is temporarily unavailable         |
| 9        | DENIED              | Role or policy does not allow the request           |
| 10       | TIMEOUT             | Operation did not complete before its deadline      |
| 11       | INVALID_STATE       | Request is not allowed in the current machine state |
| 12       | DUPLICATE_SEQUENCE  | Unsafe duplicate request                            |
| 13       | INTERNAL            | Receiver encountered an internal failure            |

# Message namespaces

| **Range**        | **Domain**                  | **Examples**                                                             |
|------------------|-----------------------------|--------------------------------------------------------------------------|
| 0x0000 to 0x00FF | Protocol and link           | HELLO READY PING PONG HEARTBEAT ACK NACK ERROR CAPABILITIES              |
| 0x0100 to 0x01FF | Robot state                 | ROBOT_STATUS BALANCE_STATE MODE_STATE FAULT_STATE                        |
| 0x0200 to 0x02FF | Motion and motors           | DRIVE_COMMAND VELOCITY_REQUEST MOTOR_STATUS ESTOP ESTOP_CLEAR_REQUEST    |
| 0x0300 to 0x03FF | Sensors                     | IMU_DATA ENCODER_DATA LIDAR_SUMMARY SENSOR_HEALTH                        |
| 0x0900 to 0x09FF | Perception and tracking     | TARGET_TRACKS TARGET_EVENT CAMERA_STATUS TRACKER_STATUS SNAPSHOT_REQUEST |
| 0x0A00 to 0x0AFF | Localization and navigation | ODOMETRY POSE_ESTIMATE LOCALIZATION_STATUS NAV_COMMAND NAV_STATUS        |
| 0x0400 to 0x04FF | Battery and power           | BATTERY_STATUS POWER_STATUS SHUTDOWN_REQUEST SHUTDOWN_STATE              |
| 0x0500 to 0x05FF | Configuration               | CONFIG_GET CONFIG_VALUE CONFIG_SET CONFIG_RESULT CONFIG_SCHEMA           |
| 0x0600 to 0x06FF | Audio and speech            | SPEAK PLAY_SOUND STOP_AUDIO AUDIO_STATUS                                 |
| 0x0700 to 0x07FF | User interface              | UI_EVENT DISPLAY_STATE OPERATOR_NOTICE                                   |
| 0x0800 to 0x08FF | Logging and debug           | LOG_MESSAGE METRICS DEBUG_COMMAND TRACE_CONTROL                          |
| 0x0B00 to 0x0FFF | Reserved common services    | Allocate through protocol registry                                       |
| 0x1000 to 0xEFFF | Application specific        | Project modules and experimental services                                |
| 0xF000 to 0xFFFF | Development and vendor      | Never depend on these identifiers for portable behavior                  |

# Core message registry

| **ID** | **Name**            | **Direction and purpose**                                                                                                          |
|--------|---------------------|------------------------------------------------------------------------------------------------------------------------------------|
| 0x0001 | HELLO               | Both directions. Advertise role, versions, capabilities, boot ID, implementation version, and limits.                              |
| 0x0002 | READY               | Both directions. Declare negotiation complete.                                                                                     |
| 0x0003 | HEARTBEAT           | Both directions. Liveness, monotonic time, state summary, and health flags.                                                        |
| 0x0004 | ACK                 | Both directions. Application acceptance.                                                                                           |
| 0x0005 | NACK                | Both directions. Application rejection.                                                                                            |
| 0x0006 | ERROR               | Both directions. Session or generic structured error.                                                                              |
| 0x0007 | PING                | Both directions. Echo token and sender time.                                                                                       |
| 0x0008 | PONG                | Both directions. Echo token and timing data.                                                                                       |
| 0x0100 | ROBOT_STATUS        | Bone to Pi event. Overall mode, enabled state, faults, uptime, and control state.                                                  |
| 0x0101 | BALANCE_STATE       | Bone to Pi event. Balance controller state and summarized attitude.                                                                |
| 0x0200 | DRIVE_COMMAND       | Pi to Bone. Normalised steer and drive request with expiry (`ttl_ms`); never raw motor power. Implemented as an unacknowledged EVENT at ~10 Hz: each command supersedes the last and expiry, not ACK, bounds its effect. |
| 0x0201 | ESTOP               | Either direction request. Requests safe stop; Bone owns final safety action.                                                       |
| 0x0202 | ESTOP_CLEAR_REQUEST | Pi to Bone request. Request only; Bone validates local conditions and policy.                                                      |
| 0x0203 | MOTOR_STATUS        | Bone to Pi event. Motor controller state and faults.                                                                               |
| 0x0300 | IMU_DATA            | Bone to Pi event. Selected raw or filtered IMU sample.                                                                             |
| 0x0301 | ENCODER_DATA        | Bone to Pi event. Positions, velocities, counts, and sample time.                                                                  |
| 0x0400 | BATTERY_STATUS      | Bone to Pi event. Voltage, current, estimate, warning, and cutoff state.                                                           |
| 0x0401 | SHUTDOWN_REQUEST    | Either direction request. Coordinates orderly service shutdown; local protection remains independent.                              |
| 0x0500 | CONFIG_GET          | Either direction request. Read named or numeric setting.                                                                           |
| 0x0501 | CONFIG_VALUE        | Response with value, type, revision, and persistence state.                                                                        |
| 0x0502 | CONFIG_SET          | Either direction request. Validate and apply a setting under ownership rules.                                                      |
| 0x0503 | CONFIG_RESULT       | Response with applied value, revision, and restart requirement.                                                                    |
| 0x0600 | SPEAK               | Bone to Pi request or either direction by capability. Text, priority, voice hint, and expiry.                                      |
| 0x0601 | PLAY_SOUND          | Request a named sound or URI allowed by local policy.                                                                              |
| 0x0900 | TARGET_TRACKS       | Pi to Bone event. Current confirmed targets with camera, track ID, class, confidence, normalized box, age, and sample time.        |
| 0x0901 | TARGET_EVENT        | Pi to Bone event. Track confirmed, updated milestone, lost, line crossing, or dwell event.                                         |
| 0x0902 | CAMERA_STATUS       | Pi to Bone event or response. Per camera health, frame age, capture state, and processing rate.                                    |
| 0x0903 | TRACKER_STATUS      | Pi to Bone event or response. Hailo model, NPU health, active cameras, and inference health.                                       |
| 0x0904 | SNAPSHOT_REQUEST    | Bone to Pi request. Ask hailo-tracker to save a snapshot; response returns status and local reference, not image bytes by default. |
| 0x0A00 | ODOMETRY            | Bone to Pi event. Timestamped wheel based pose delta or velocity with quality data for ROS fusion.                                 |
| 0x0A01 | POSE_ESTIMATE       | Pi to Bone event. Optional map or odom frame pose summary with age, source, quality, and covariance.                               |
| 0x0A02 | LOCALIZATION_STATUS | Pi to Bone event. SLAM or localization state, map identity, tracking quality, and fault reason.                                    |
| 0x0A03 | NAV_COMMAND         | Pi to Bone request. Constrained high level motion request with velocity limits and a short expiry.                                 |
| 0x0A04 | NAV_STATUS          | Bone to Pi event or response. Acceptance, active command, expiry, inhibition, and completion state.                                |
| 0x0700 | UI_EVENT            | Pi to Bone event. Operator or interface action, never a direct low level control primitive.                                        |
| 0x0800 | LOG_MESSAGE         | Both directions event. Structured severity, source, time, code, and text.                                                          |

## Registry governance

One checked in registry file is authoritative for identifiers, names, direction, minimum protocol version, payload schema, ACK policy, idempotence, permission, and deprecation state. Generated C and Python constants come from that registry. Identifiers are never reused, including after deprecation.

# Safety and authority boundaries

| **Concern**            | **Required rule**                                                                                                                            |
|------------------------|----------------------------------------------------------------------------------------------------------------------------------------------|
| Balance and motor loop | Runs entirely on the BeagleBone. No network callback may execute inside or block the control loop.                                           |
| Pi absence             | Normal supported condition. The Bone remains safe and useful within its local capabilities.                                                  |
| Drive requests         | Contain an explicit validity duration. The Bone stops honoring stale accessory commands after expiry or link loss.                           |
| Emergency stop         | The Bone treats a valid ESTOP request as urgent, but physical and local e stop paths remain primary. Clearing requires Bone side validation. |
| Power shutdown         | Orderly shutdown messages coordinate software only. Hardware undervoltage protection and local power controls remain independent.            |
| Configuration          | Each setting has an owner, type, range, persistence policy, and allowed machine states. Unsafe changes are rejected.                         |
| Time                   | Safety deadlines use local monotonic clocks. Remote timestamps are diagnostic until clock relationship is estimated.                         |
| Overload               | Telemetry may be dropped or coalesced. Safety state and link control messages use bounded priority queues.                                   |

## Command permissions

HELLO declares a peer role. A policy table maps role and message type to allowed directions. The Pi may request high level actions, configuration changes, speech, and information according to policy. It does not gain permission merely because it can form a packet. Unknown or denied commands receive a structured error when safe to do so.

# Concurrency queues and backpressure

- Use separate bounded queues for high priority control, request response traffic, and telemetry.

- Never hold the transmit mutex while invoking application code.

- A send function either enqueues the complete immutable packet or returns a defined queue full error.

- Partial socket writes remain owned by the transport until complete or failed. send once is not sufficient.

- Telemetry producers state whether samples may be dropped, replaced by the newest sample, or batched.

- High priority does not mean unbounded. A flood limiter protects the process from a malfunctioning peer.

- Queue depths, high water marks, drops, and oldest item age are observable metrics.

# Timing and timestamps

Each peer reports boot ID, monotonic time, and optional wall clock time. Operational durations and expiry checks use the receiving peer’s monotonic clock. PING and PONG can estimate round trip delay and clock offset for correlating logs, but version 2 does not promise synchronized clocks. Sensor payloads include a source monotonic sample timestamp and sample sequence where ordering matters.

# Configuration behavior

Configuration messages use stable numeric keys with optional human readable names. Values are typed as signed integer, unsigned integer, float, boolean, string, byte array, or structured value. CONFIG_SET includes the expected current revision when lost updates matter. The response returns the actual applied value because the Bone may clamp or normalize an input. Settings declare whether they are volatile, persistent, or require restart.

# Diagnostics and observability

| **Category** | **Minimum counters or fields**                                                          |
|--------------|-----------------------------------------------------------------------------------------|
| Connection   | Connects, disconnects, current state, session age, peer boot ID, negotiated version     |
| Receive      | Bytes, frames, CRC errors, framing errors, length errors, unsupported types, duplicates |
| Transmit     | Bytes, frames, queue depth, partial writes, queue full failures, retries                |
| Requests     | Outstanding count, completions, timeouts, NACKs, latency histogram                      |
| Heartbeat    | Last receive age, missed deadlines, round trip estimate                                 |
| Application  | Per message counts, handler errors, dropped or coalesced telemetry                      |

# Compatibility with ESPin32 version 1

The existing ESPin32 frame is retained as a legacy protocol, not ambiguously mixed into a version 2 byte stream. Its format is 0xAA, one byte length, one byte command, payload, CRC 16 XMODEM high byte then low byte, and 0x55. Its practical maximum payload is 249 bytes in the current code. The repository documentation that describes a 1024 byte packet must be corrected or explicitly labeled as an older design.

| **Topic**       | **Version 1**             | **Version 2**                                |
|-----------------|---------------------------|----------------------------------------------|
| Version field   | None                      | Explicit one byte version                    |
| Length          | One byte                  | Two byte payload length                      |
| Message ID      | One byte command          | Two byte namespaced type                     |
| Flags           | None                      | ACK response error event idempotent priority |
| Sequence        | None                      | Two byte correlation value                   |
| CRC             | CRC 16 XMODEM             | Same algorithm                               |
| Parser          | Byte state machine        | Same approach with expanded header           |
| Transport       | BLE or serial use         | Any reliable or raw byte stream with adapter |
| Maximum payload | 249 bytes in current code | Negotiated default 4096 bytes                |

A transport endpoint is configured for one protocol mode: version 1, version 2, or explicit auto detection during a bounded startup window. Production Pi to Bone TCP uses version 2 only. A bridge may map selected version 1 commands into version 2 identifiers; unmapped commands return an error. The bridge does not pretend that version 1 supports correlation or large payloads.

# Versioning and evolution

- Protocol version changes only when framing or global interpretation becomes incompatible.

- Adding a new message type does not change the protocol version.

- Append only optional payload fields are allowed when the message definition says receivers may ignore a trailing suffix.

- The meaning, units, or type of an existing field never changes. Add a new field or message instead.

- Capability bits advertise optional behavior. Absence means unsupported, not false success.

- Deprecated identifiers remain reserved and documented. They are never assigned a new meaning.

- HELLO carries minimum and maximum supported versions so a future peer can negotiate or fail clearly.

# Security scope

The baseline assumes a physically local private RNDIS network and does not add encryption or authentication inside the packet protocol. The service binds only to the configured gadget interface or address by default. It does not listen on all network interfaces unless explicitly configured. If this protocol crosses an untrusted network, use an authenticated encrypted transport such as TLS or a secure tunnel; CRC detects corruption but is not a security mechanism.

# Reference API

/\* transport independent C surface \*/  
int robot_link_send(robot_link_t \*link, const robot_message_t \*msg);  
int robot_link_request(robot_link_t \*link, const robot_message_t \*request,  
uint32_t timeout_ms, robot_response_cb callback, void \*ctx);  
int robot_link_subscribe(robot_link_t \*link, uint16_t type,  
robot_message_cb callback, void \*ctx);  
robot_link_state_t robot_link_state(const robot_link_t \*link);

\# Python accessory surface  
await link.connect()  
link.on(MessageType.BATTERY_STATUS, handle_battery)  
reply = await link.request(MessageType.CONFIG_GET, payload, timeout=1.0)  
await link.send(MessageType.SPEAK, payload, ack_required=True)

# Proposed source layout

common/robot_link/  
protocol/packet.c packet.h crc16.c crc16.h parser.c parser.h  
service/link.c link.h dispatch.c dispatch.h request_table.c  
registry/messages.yaml generated/message_ids.h generated/message_ids.py  
transport/transport.h tcp_posix.c serial_posix.c  
tests/test_vectors.json parser_tests.c interoperability_test.py  
balance_bot/src/robot_link_adapter.c  
pi/robot-linkd/daemon.py local_server.py permissions.yaml robot-linkd.service  
pi/python/robot_link/client.py messages.py  
pi/ros2_ws/src/robot_link_bridge/  
hailo-tracker/robot_link_adapter.py

# Implementation phases

| **Phase**             | **Deliverable**                                                           | **Exit condition**                                                                                               |
|-----------------------|---------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------|
| 1 Protocol core       | Portable CRC, builder, parser, test vectors, fuzz target                  | C and Python produce and parse identical frames; corrupt and fragmented input tests pass.                        |
| 2 TCP transport       | Bone server, Pi reconnecting client, read and write loops                 | Repeated unplug, reboot, partial write, and reconnect tests pass without process restart.                        |
| 3 Session service     | HELLO READY heartbeat capabilities request table ACK NACK                 | Negotiation, timeout, duplicate, peer restart, and link loss behaviors pass.                                     |
| 4 Initial messages    | Robot status battery status speak log config get and set                  | End to end handlers work with schema validation and permissions.                                                 |
| 5 Hailo integration   | Python local client and hailo-tracker adapter                             | Tracker events and health reach the Bone without blocking capture, inference, rendering, or web service threads. |
| 6 Machine integration | Balance bot adapter, bounded queues, command expiry                       | Control loop timing is unaffected and stale commands are rejected.                                               |
| 7 ROS and SLAM        | ROS bridge, lidar driver, transform tree, odometry, IMU, and slam_toolbox | A repeatable map is produced while robot-linkd and balance control remain independent of ROS lifecycle.          |
| 8 Expansion           | Navigation UI audio and optional legacy bridge                            | Each addition updates registry, tests, and this document change log if required.                                 |

# Verification strategy

- Known answer CRC vectors including empty and maximum sized payloads.

- Golden binary frames shared by C, C++, and Python implementations.

- Parser tests for every possible input split, multiple frames per read, garbage prefix, truncated frame, bad length, bad CRC, bad delimiter, and recovery into the next valid frame.

- Property and fuzz tests proving bounded memory use and no crashes on arbitrary input.

- Socket tests for short reads, short writes, EINTR, EAGAIN, peer close, half close, reconnect, and peer restart.

- Request tests for success, NACK, typed error, timeout, sequence wrap, duplicate requests, and session cancellation.

- Load tests showing that telemetry floods do not delay heartbeats or control messages beyond their budgets.

- Machine tests confirming that unplugging or rebooting the Pi never disables local safety and that accessory drive requests expire.

- Compatibility tests against captured ESPin32 version 1 frames when the bridge is implemented.

# Definition of done requirements matrix

| **ID** | **Requirement**                                                       | **Status** |
|--------|-----------------------------------------------------------------------|------------|
| R01    | Bone is autonomous and acts as TCP server                             | Required   |
| R02    | Pi is optional and reconnects automatically                           | Required   |
| R03    | Persistent bidirectional TCP over RNDIS                               | Required   |
| R04    | Transport independent packet core                                     | Required   |
| R05    | 0xAA and 0x55 delimiters                                              | Required   |
| R06    | CRC 16 XMODEM high byte first                                         | Required   |
| R07    | Explicit protocol version                                             | Required   |
| R08    | 16 bit payload length with negotiated bounded maximum                 | Required   |
| R09    | 16 bit namespaced message identifier                                  | Required   |
| R10    | Flags for ACK response error event idempotence and priority           | Required   |
| R11    | 16 bit request response sequence correlation                          | Required   |
| R12    | Incremental parser handles arbitrary chunking                         | Required   |
| R13    | Parser resynchronizes after corruption                                | Required   |
| R14    | HELLO capability and version negotiation                              | Required   |
| R15    | READY gate before application traffic                                 | Required   |
| R16    | Heartbeat and link loss detection                                     | Required   |
| R17    | Pending requests fail on disconnect                                   | Required   |
| R18    | ACK NACK and structured errors                                        | Required   |
| R19    | Typed payload encoding with defined byte order and units              | Required   |
| R20    | No native C structs on wire                                           | Required   |
| R21    | Central registry generates language constants                         | Required   |
| R22    | Message IDs never reused                                              | Required   |
| R23    | Bounded prioritized queues and backpressure                           | Required   |
| R24    | Correct handling of partial socket writes                             | Required   |
| R25    | Machine callbacks isolated from parser and network thread             | Required   |
| R26    | Drive commands expire                                                 | Required   |
| R27    | E stop clear remains Bone validated                                   | Required   |
| R28    | Hardware power safety remains independent                             | Required   |
| R29    | Role and direction permission checks                                  | Required   |
| R30    | Diagnostics counters and structured logs                              | Required   |
| R31    | Version 1 behavior documented and isolated                            | Required   |
| R32    | C and Python interoperability tests                                   | Required   |
| R33    | Fragmentation corruption reconnect and load tests                     | Required   |
| R34    | Pi unplug and reboot safety tests                                     | Required   |
| R35    | Service supervision and clean shutdown                                | Required   |
| R36    | robot-linkd runs beside and independently of hailo-tracker            | Required   |
| R37    | Multiple Pi services share one Bone TCP session through local IPC     | Required   |
| R38    | Hailo target messages preserve camera and track identity              | Required   |
| R39    | ROS 2 remains optional and confined to the Pi                         | Required   |
| R40    | ROS bridge maps IMU odometry pose and expiring navigation commands    | Required   |
| R41    | ROS or SLAM failure cannot reset the robot link or machine controller | Required   |

# Open implementation decisions

These choices do not change the architecture, but they must be resolved before coding their affected feature.

| **Decision**            | **Recommended starting point**                                                                              |
|-------------------------|-------------------------------------------------------------------------------------------------------------|
| Exact RNDIS addresses   | Use the addresses already assigned on the robot and expose them in service configuration.                   |
| Local IPC payload       | Start with length prefixed typed messages over a Unix domain socket and reuse the central message registry. |
| Payload schema source   | Use a small YAML registry plus hand reviewed generated encoders and decoders.                               |
| Maximum telemetry rates | Define per message after measuring what the UI, logging, and control diagnostics actually need.             |
| Legacy bridge           | Implement only for ESP32 devices that must talk directly to the Linux link.                                 |
| ROS deployment          | Use ROS 2 Jazzy on Ubuntu 24.04 arm64; start with lidar, transforms, odometry, IMU, and slam_toolbox.       |
| TCP authentication      | None on private gadget interface; require TLS or tunnel if routing expands.                                 |

# Change control

| **Version** | **Date**          | **Change**                                                                                              |
|-------------|-------------------|---------------------------------------------------------------------------------------------------------|
| 1.0         | 19 September 2026 | Initial complete design baseline for ESPin32 packet protocol version 2 over Pi to BeagleBone RNDIS TCP. |

Future revisions must preserve this table and add a row describing every intentional requirement change. Implementation pull requests should cite requirement IDs from the definition of done matrix.
