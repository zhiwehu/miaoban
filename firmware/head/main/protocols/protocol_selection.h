#pragma once

namespace voice {
enum class ProtocolKind { Mqtt, Websocket, Offline };

// Cached factory MQTT credentials must not become an implicit fallback when
// the user's configured OTA service is unavailable.
inline ProtocolKind SelectProtocol(bool fresh_mqtt, bool fresh_websocket, bool cached_websocket) {
    if (fresh_mqtt) return ProtocolKind::Mqtt;
    if (fresh_websocket || cached_websocket) return ProtocolKind::Websocket;
    return ProtocolKind::Offline;
}
}
