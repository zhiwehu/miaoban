// Board pins and angle conversion verified against this unit's factory image
// and live GPIO-matrix readback. No unverified third-party motor BSP is used.
#include "motion.h"
#include "base_protocol.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "driver/uart.h"
#include "driver/usb_serial_jtag_vfs.h"
#include <string>
#include <unistd.h>
#include "esp_adc/adc_oneshot.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include <limits>
static const char* TAG="miaoban_base";
static adc_oneshot_unit_handle_t adc;
static adc_cali_handle_t calibration;
static int previous_direction=0;
struct Snapshot { float actual,goal;bool active;int fault; };
static QueueHandle_t snapshots;
static void LogTask(void*) {
    Snapshot s;
    while(true)if(xQueueReceive(snapshots,&s,portMAX_DELAY)==pdTRUE)
        ESP_LOGI(TAG,"actual=%.1f target=%.1f busy=%d fault=%d",s.actual,s.goal,s.active,s.fault);
}
static void Drive(int signed_duty) {
    int direction=(signed_duty>0)-(signed_duty<0);
    // Break before make; never energise both H-bridge inputs.
    ledc_set_duty(LEDC_LOW_SPEED_MODE,LEDC_CHANNEL_0,0);
    ledc_set_duty(LEDC_LOW_SPEED_MODE,LEDC_CHANNEL_1,0);
    ledc_update_duty(LEDC_LOW_SPEED_MODE,LEDC_CHANNEL_0);
    ledc_update_duty(LEDC_LOW_SPEED_MODE,LEDC_CHANNEL_1);
    gpio_set_level(GPIO_NUM_5,0);
    if (direction && previous_direction && direction!=previous_direction) {
        previous_direction=direction;
        return; // One full 20ms tick of dead time on reversal.
    }
    previous_direction=direction;
    if (!direction) return;
    auto channel=direction>0?LEDC_CHANNEL_0:LEDC_CHANNEL_1;
    ledc_set_duty(LEDC_LOW_SPEED_MODE,channel,std::abs(signed_duty));
    ledc_update_duty(LEDC_LOW_SPEED_MODE,channel);
    gpio_set_level(GPIO_NUM_5,1);
}
static float Angle() {
    if (!calibration) return std::numeric_limits<float>::quiet_NaN();
    int sum=0;
    for (int i=0;i<16;++i) {
        int raw,mv;
        if (adc_oneshot_read(adc,ADC_CHANNEL_0,&raw)!=ESP_OK ||
            adc_cali_raw_to_voltage(calibration,raw,&mv)!=ESP_OK)
            return std::numeric_limits<float>::quiet_NaN();
        sum+=mv;
    }
    float mv=sum/16.0f;
    if (mv<48 || mv>2780) return std::numeric_limits<float>::quiet_NaN();
    return (mv-30)*300.0f/2770.0f;
}
static void Status(float angle,const miaoban::Motion& motion) {
    uint16_t value=std::isfinite(angle)?std::lround(angle*10):0xffff;
    uint8_t frame[]={0xAA,0x55,0,5,0x81,static_cast<uint8_t>(value>>8),
        static_cast<uint8_t>(value),static_cast<uint8_t>(motion.active),
        static_cast<uint8_t>(motion.fault),0};
    for (int i=4;i<9;++i) frame[9]+=frame[i];
    uart_write_bytes(UART_NUM_1,frame,sizeof(frame));
}
extern "C" void app_main() {
    gpio_config_t output={};
    output.pin_bit_mask=(1ULL<<5)|(1ULL<<2);
    output.mode=GPIO_MODE_OUTPUT;
    ESP_ERROR_CHECK(gpio_config(&output));
    gpio_set_level(GPIO_NUM_5,0);
    gpio_set_level(GPIO_NUM_2,1);
    ledc_timer_config_t timer={};
    timer.speed_mode=LEDC_LOW_SPEED_MODE;timer.duty_resolution=LEDC_TIMER_10_BIT;
    timer.timer_num=LEDC_TIMER_0;timer.freq_hz=25000;timer.clk_cfg=LEDC_USE_APB_CLK;
    ESP_ERROR_CHECK(ledc_timer_config(&timer));
    for (int i=0;i<2;++i) {
        ledc_channel_config_t channel={};
        channel.gpio_num=i==0?4:10;channel.speed_mode=LEDC_LOW_SPEED_MODE;
        channel.channel=static_cast<ledc_channel_t>(i);channel.timer_sel=LEDC_TIMER_0;
        ESP_ERROR_CHECK(ledc_channel_config(&channel));
    }
    Drive(0);
    adc_oneshot_unit_init_cfg_t unit={};unit.unit_id=ADC_UNIT_1;
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&unit,&adc));
    adc_oneshot_chan_cfg_t channel={};channel.atten=ADC_ATTEN_DB_12;channel.bitwidth=ADC_BITWIDTH_DEFAULT;
    ESP_ERROR_CHECK(adc_oneshot_config_channel(adc,ADC_CHANNEL_0,&channel));
    adc_cali_curve_fitting_config_t cal={};cal.unit_id=ADC_UNIT_1;
    cal.chan=ADC_CHANNEL_0;cal.atten=ADC_ATTEN_DB_12;cal.bitwidth=ADC_BITWIDTH_DEFAULT;
    if (adc_cali_create_scheme_curve_fitting(&cal,&calibration)!=ESP_OK)
        ESP_LOGE(TAG,"ADC calibration unavailable; motion disabled");
    uart_config_t uart={};uart.baud_rate=115200;uart.data_bits=UART_DATA_8_BITS;
    uart.parity=UART_PARITY_DISABLE;uart.stop_bits=UART_STOP_BITS_1;uart.source_clk=UART_SCLK_DEFAULT;
    ESP_ERROR_CHECK(uart_driver_install(UART_NUM_1,1024,512,0,nullptr,0));
    ESP_ERROR_CHECK(uart_param_config(UART_NUM_1,&uart));
    ESP_ERROR_CHECK(uart_set_pin(UART_NUM_1,21,20,-1,-1));
    miaoban::Motion motion;
    miaoban::BaseFrame parser;
    float actual=Angle();
    if (miaoban::Motion::Valid(actual)) motion.Request(miaoban::kCenter,actual,esp_timer_get_time()/1000);
    else motion.fault=miaoban::SENSOR;
    ESP_LOGI(TAG,"25kHz, limits %.0f..%.0f, home %.0f, actual %.1f",miaoban::kMin,miaoban::kMax,miaoban::kCenter,actual);
    // Reuse the boot console's nonblocking VFS; do not reinstall the USB driver.
    usb_serial_jtag_vfs_use_nonblocking();
    snapshots=xQueueCreate(1,sizeof(Snapshot));
    configASSERT(snapshots);
    xTaskCreate(LogTask,"base_log",3072,nullptr,1,nullptr);
    vTaskPrioritySet(nullptr,5);
    std::string diagnostic;
    int64_t manual_until=esp_timer_get_time()/1000+5000;
    int64_t last_status=0,last_log=0;
    TickType_t wake=xTaskGetTickCount();
    while (true) {
        int64_t now=esp_timer_get_time()/1000;
        actual=Angle();
        // Physical USB service commands for bounded commissioning tests.
        // No network or LLM can invoke these commands.
        char usb_input[32];
        int received=read(STDIN_FILENO,usb_input,sizeof(usb_input));
        for(int i=0;i<received;++i){
            char c=usb_input[i];
            if(c=='\n'){
                if(diagnostic=="stop"){motion.Stop(actual);manual_until=now+10000;}
                else if(diagnostic=="step 10" || diagnostic=="step -10") {
                    const int delta=diagnostic=="step 10"?10:-10;
                    motion.Request(actual+delta,actual,now);
                    manual_until=now+10000;
                } else if(diagnostic=="home") {
                    motion.Request(miaoban::kCenter,actual,now);manual_until=now+6000;
                }
                diagnostic.clear();
            } else if(c!='\r' && diagnostic.size()<16)diagnostic+=c;
        }
        uint8_t data[128];int len=uart_read_bytes(UART_NUM_1,data,sizeof(data),0);
        int latest=-1;bool query=false,stop=false;
        for (int i=0;i<len;++i) {
            if (!parser.Feed(data[i])) continue;
            auto& frame=parser.bytes;
            if (frame[3]==3 && frame[4]==1) latest=(frame[5]<<8)|frame[6];
            if (frame[3]==1 && frame[4]==0x10) query=true;
            if (frame[3]==1 && frame[4]==0x11) stop=true;
        }
        if (stop) motion.Stop(actual);
        else if (latest>=0 && now>=manual_until) motion.Request(latest,actual,now);
        Drive(motion.Tick(actual,now));
        if (query || now-last_status>=100) {Status(actual,motion);last_status=now;}
        if (now-last_log>=1000) {
            Snapshot snapshot={actual,motion.goal,motion.active,static_cast<int>(motion.fault)};
            xQueueOverwrite(snapshots,&snapshot);
            last_log=now;
        }
        vTaskDelayUntil(&wake,pdMS_TO_TICKS(20));
    }
}
