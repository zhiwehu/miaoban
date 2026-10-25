#include "../firmware/head/main/protocols/protocol_selection.h"
#include <cassert>
int main() {
    using voice::ProtocolKind;
    // A transient outage must preserve an explicitly configured WebSocket path.
    assert(voice::SelectProtocol(false, false, true) == ProtocolKind::Websocket);
    // No configuration means offline; factory MQTT credentials are not authorization to fall back.
    assert(voice::SelectProtocol(false, false, false) == ProtocolKind::Offline);
    assert(voice::SelectProtocol(false, true, false) == ProtocolKind::Websocket);
    assert(voice::SelectProtocol(false, true, true) == ProtocolKind::Websocket);
    // MQTT is permitted only when the selected OTA service explicitly supplies it.
    assert(voice::SelectProtocol(true, false, false) == ProtocolKind::Mqtt);
    assert(voice::SelectProtocol(true, true, true) == ProtocolKind::Mqtt);
}
