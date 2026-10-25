#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
namespace miaoban {
// AA55, big-endian payload length, command + data, additive checksum.
class BaseFrame {
public:
    bool Feed(uint8_t byte) {
        if (used==0 && byte!=0xAA) return false;
        if (used==1 && byte!=0x55) { used=byte==0xAA?1:0; return false; }
        bytes[used++]=byte;
        if (used==4 && (bytes[2]!=0 || bytes[3]<1 || bytes[3]>8)) { used=0; return false; }
        if (used>=5 && used==static_cast<size_t>(bytes[3]+5)) {
            uint8_t sum=0;
            for (size_t i=4;i<used-1;++i) sum+=bytes[i];
            bool valid=sum==bytes[used-1];
            used=0;
            return valid;
        }
        return false;
    }
    std::array<uint8_t,13> bytes{};
private:
    size_t used=0;
};
}
