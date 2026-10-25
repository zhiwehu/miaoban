#include "../firmware/shared/base_protocol.h"
#include <cassert>
int main(){
    miaoban::BaseFrame parser;
    unsigned char good[]={0xAA,0x55,0,3,1,0,162,163};
    for(int i=0;i<7;++i)assert(!parser.Feed(good[i]));
    assert(parser.Feed(good[7]));assert(parser.bytes[6]==162);
    unsigned char corrupt[]={0xAA,0x55,0,3,1,0,162,0};
    for(auto c:corrupt)assert(!parser.Feed(c));
    unsigned char badlen[]={0xAA,0x55,0,255,0,0};
    for(auto c:badlen)assert(!parser.Feed(c));
    for(int j=0;j<2;++j)for(int i=0;i<8;++i)assert(parser.Feed(good[i])==(i==7));
}
