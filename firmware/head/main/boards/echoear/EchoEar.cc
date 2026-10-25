#include "wifi_board.h"
#include "application.h"
#include "echoear_audio_codec.h"
#include "direction_math.h"
#include "settings.h"
#include "mcp_server.h"
#include <atomic>
#include "../../../../shared/base_protocol.h"
#include <array>
#include <freertos/queue.h>
#include "display/lcd_display.h"
#include "display/emote_display.h"
#include "application.h"
#include "button.h"
#include "config.h"
#include "backlight.h"

#include <wifi_station.h>
#include <esp_log.h>

#include <driver/i2c_master.h>
#include <driver/i2c.h>
#include "i2c_device.h"
#include <esp_afe_sr_models.h>
#include <esp_afe_doa.h>
#include <esp_vad.h>
#include <esp_timer.h>
#include <math.h>
#include <esp_lcd_panel_io.h>
#include <esp_lcd_panel_ops.h>
#include <esp_lcd_st77916.h>
#include "esp_lcd_touch_cst816s.h"
#include "touch.h"
#include "bmi270_imu.h"

#include "driver/temperature_sensor.h"
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/task.h>

#define TAG "EchoEar"
static std::atomic<int> g_doa_last_angle{0},g_doa_raw{90},g_front_angle{90};
static std::atomic<int64_t> g_doa_last_ms{0},g_base_feedback_ms{0};
static std::atomic<int> g_base_actual{-1},g_base_fault{-1};
static std::atomic<bool> g_base_moving{false},g_tracking_enabled{true};


temperature_sensor_handle_t temp_sensor = NULL;
static const st77916_lcd_init_cmd_t vendor_specific_init_yysj[] = {
    {0xF0, (uint8_t []){0x28}, 1, 0},
    {0xF2, (uint8_t []){0x28}, 1, 0},
    {0x73, (uint8_t []){0xF0}, 1, 0},
    {0x7C, (uint8_t []){0xD1}, 1, 0},
    {0x83, (uint8_t []){0xE0}, 1, 0},
    {0x84, (uint8_t []){0x61}, 1, 0},
    {0xF2, (uint8_t []){0x82}, 1, 0},
    {0xF0, (uint8_t []){0x00}, 1, 0},
    {0xF0, (uint8_t []){0x01}, 1, 0},
    {0xF1, (uint8_t []){0x01}, 1, 0},
    {0xB0, (uint8_t []){0x56}, 1, 0},
    {0xB1, (uint8_t []){0x4D}, 1, 0},
    {0xB2, (uint8_t []){0x24}, 1, 0},
    {0xB4, (uint8_t []){0x87}, 1, 0},
    {0xB5, (uint8_t []){0x44}, 1, 0},
    {0xB6, (uint8_t []){0x8B}, 1, 0},
    {0xB7, (uint8_t []){0x40}, 1, 0},
    {0xB8, (uint8_t []){0x86}, 1, 0},
    {0xBA, (uint8_t []){0x00}, 1, 0},
    {0xBB, (uint8_t []){0x08}, 1, 0},
    {0xBC, (uint8_t []){0x08}, 1, 0},
    {0xBD, (uint8_t []){0x00}, 1, 0},
    {0xC0, (uint8_t []){0x80}, 1, 0},
    {0xC1, (uint8_t []){0x10}, 1, 0},
    {0xC2, (uint8_t []){0x37}, 1, 0},
    {0xC3, (uint8_t []){0x80}, 1, 0},
    {0xC4, (uint8_t []){0x10}, 1, 0},
    {0xC5, (uint8_t []){0x37}, 1, 0},
    {0xC6, (uint8_t []){0xA9}, 1, 0},
    {0xC7, (uint8_t []){0x41}, 1, 0},
    {0xC8, (uint8_t []){0x01}, 1, 0},
    {0xC9, (uint8_t []){0xA9}, 1, 0},
    {0xCA, (uint8_t []){0x41}, 1, 0},
    {0xCB, (uint8_t []){0x01}, 1, 0},
    {0xD0, (uint8_t []){0x91}, 1, 0},
    {0xD1, (uint8_t []){0x68}, 1, 0},
    {0xD2, (uint8_t []){0x68}, 1, 0},
    {0xF5, (uint8_t []){0x00, 0xA5}, 2, 0},
    {0xDD, (uint8_t []){0x4F}, 1, 0},
    {0xDE, (uint8_t []){0x4F}, 1, 0},
    {0xF1, (uint8_t []){0x10}, 1, 0},
    {0xF0, (uint8_t []){0x00}, 1, 0},
    {0xF0, (uint8_t []){0x02}, 1, 0},
    {0xE0, (uint8_t []){0xF0, 0x0A, 0x10, 0x09, 0x09, 0x36, 0x35, 0x33, 0x4A, 0x29, 0x15, 0x15, 0x2E, 0x34}, 14, 0},
    {0xE1, (uint8_t []){0xF0, 0x0A, 0x0F, 0x08, 0x08, 0x05, 0x34, 0x33, 0x4A, 0x39, 0x15, 0x15, 0x2D, 0x33}, 14, 0},
    {0xF0, (uint8_t []){0x10}, 1, 0},
    {0xF3, (uint8_t []){0x10}, 1, 0},
    {0xE0, (uint8_t []){0x07}, 1, 0},
    {0xE1, (uint8_t []){0x00}, 1, 0},
    {0xE2, (uint8_t []){0x00}, 1, 0},
    {0xE3, (uint8_t []){0x00}, 1, 0},
    {0xE4, (uint8_t []){0xE0}, 1, 0},
    {0xE5, (uint8_t []){0x06}, 1, 0},
    {0xE6, (uint8_t []){0x21}, 1, 0},
    {0xE7, (uint8_t []){0x01}, 1, 0},
    {0xE8, (uint8_t []){0x05}, 1, 0},
    {0xE9, (uint8_t []){0x02}, 1, 0},
    {0xEA, (uint8_t []){0xDA}, 1, 0},
    {0xEB, (uint8_t []){0x00}, 1, 0},
    {0xEC, (uint8_t []){0x00}, 1, 0},
    {0xED, (uint8_t []){0x0F}, 1, 0},
    {0xEE, (uint8_t []){0x00}, 1, 0},
    {0xEF, (uint8_t []){0x00}, 1, 0},
    {0xF8, (uint8_t []){0x00}, 1, 0},
    {0xF9, (uint8_t []){0x00}, 1, 0},
    {0xFA, (uint8_t []){0x00}, 1, 0},
    {0xFB, (uint8_t []){0x00}, 1, 0},
    {0xFC, (uint8_t []){0x00}, 1, 0},
    {0xFD, (uint8_t []){0x00}, 1, 0},
    {0xFE, (uint8_t []){0x00}, 1, 0},
    {0xFF, (uint8_t []){0x00}, 1, 0},
    {0x60, (uint8_t []){0x40}, 1, 0},
    {0x61, (uint8_t []){0x04}, 1, 0},
    {0x62, (uint8_t []){0x00}, 1, 0},
    {0x63, (uint8_t []){0x42}, 1, 0},
    {0x64, (uint8_t []){0xD9}, 1, 0},
    {0x65, (uint8_t []){0x00}, 1, 0},
    {0x66, (uint8_t []){0x00}, 1, 0},
    {0x67, (uint8_t []){0x00}, 1, 0},
    {0x68, (uint8_t []){0x00}, 1, 0},
    {0x69, (uint8_t []){0x00}, 1, 0},
    {0x6A, (uint8_t []){0x00}, 1, 0},
    {0x6B, (uint8_t []){0x00}, 1, 0},
    {0x70, (uint8_t []){0x40}, 1, 0},
    {0x71, (uint8_t []){0x03}, 1, 0},
    {0x72, (uint8_t []){0x00}, 1, 0},
    {0x73, (uint8_t []){0x42}, 1, 0},
    {0x74, (uint8_t []){0xD8}, 1, 0},
    {0x75, (uint8_t []){0x00}, 1, 0},
    {0x76, (uint8_t []){0x00}, 1, 0},
    {0x77, (uint8_t []){0x00}, 1, 0},
    {0x78, (uint8_t []){0x00}, 1, 0},
    {0x79, (uint8_t []){0x00}, 1, 0},
    {0x7A, (uint8_t []){0x00}, 1, 0},
    {0x7B, (uint8_t []){0x00}, 1, 0},
    {0x80, (uint8_t []){0x48}, 1, 0},
    {0x81, (uint8_t []){0x00}, 1, 0},
    {0x82, (uint8_t []){0x06}, 1, 0},
    {0x83, (uint8_t []){0x02}, 1, 0},
    {0x84, (uint8_t []){0xD6}, 1, 0},
    {0x85, (uint8_t []){0x04}, 1, 0},
    {0x86, (uint8_t []){0x00}, 1, 0},
    {0x87, (uint8_t []){0x00}, 1, 0},
    {0x88, (uint8_t []){0x48}, 1, 0},
    {0x89, (uint8_t []){0x00}, 1, 0},
    {0x8A, (uint8_t []){0x08}, 1, 0},
    {0x8B, (uint8_t []){0x02}, 1, 0},
    {0x8C, (uint8_t []){0xD8}, 1, 0},
    {0x8D, (uint8_t []){0x04}, 1, 0},
    {0x8E, (uint8_t []){0x00}, 1, 0},
    {0x8F, (uint8_t []){0x00}, 1, 0},
    {0x90, (uint8_t []){0x48}, 1, 0},
    {0x91, (uint8_t []){0x00}, 1, 0},
    {0x92, (uint8_t []){0x0A}, 1, 0},
    {0x93, (uint8_t []){0x02}, 1, 0},
    {0x94, (uint8_t []){0xDA}, 1, 0},
    {0x95, (uint8_t []){0x04}, 1, 0},
    {0x96, (uint8_t []){0x00}, 1, 0},
    {0x97, (uint8_t []){0x00}, 1, 0},
    {0x98, (uint8_t []){0x48}, 1, 0},
    {0x99, (uint8_t []){0x00}, 1, 0},
    {0x9A, (uint8_t []){0x0C}, 1, 0},
    {0x9B, (uint8_t []){0x02}, 1, 0},
    {0x9C, (uint8_t []){0xDC}, 1, 0},
    {0x9D, (uint8_t []){0x04}, 1, 0},
    {0x9E, (uint8_t []){0x00}, 1, 0},
    {0x9F, (uint8_t []){0x00}, 1, 0},
    {0xA0, (uint8_t []){0x48}, 1, 0},
    {0xA1, (uint8_t []){0x00}, 1, 0},
    {0xA2, (uint8_t []){0x05}, 1, 0},
    {0xA3, (uint8_t []){0x02}, 1, 0},
    {0xA4, (uint8_t []){0xD5}, 1, 0},
    {0xA5, (uint8_t []){0x04}, 1, 0},
    {0xA6, (uint8_t []){0x00}, 1, 0},
    {0xA7, (uint8_t []){0x00}, 1, 0},
    {0xA8, (uint8_t []){0x48}, 1, 0},
    {0xA9, (uint8_t []){0x00}, 1, 0},
    {0xAA, (uint8_t []){0x07}, 1, 0},
    {0xAB, (uint8_t []){0x02}, 1, 0},
    {0xAC, (uint8_t []){0xD7}, 1, 0},
    {0xAD, (uint8_t []){0x04}, 1, 0},
    {0xAE, (uint8_t []){0x00}, 1, 0},
    {0xAF, (uint8_t []){0x00}, 1, 0},
    {0xB0, (uint8_t []){0x48}, 1, 0},
    {0xB1, (uint8_t []){0x00}, 1, 0},
    {0xB2, (uint8_t []){0x09}, 1, 0},
    {0xB3, (uint8_t []){0x02}, 1, 0},
    {0xB4, (uint8_t []){0xD9}, 1, 0},
    {0xB5, (uint8_t []){0x04}, 1, 0},
    {0xB6, (uint8_t []){0x00}, 1, 0},
    {0xB7, (uint8_t []){0x00}, 1, 0},
    {0xB8, (uint8_t []){0x48}, 1, 0},
    {0xB9, (uint8_t []){0x00}, 1, 0},
    {0xBA, (uint8_t []){0x0B}, 1, 0},
    {0xBB, (uint8_t []){0x02}, 1, 0},
    {0xBC, (uint8_t []){0xDB}, 1, 0},
    {0xBD, (uint8_t []){0x04}, 1, 0},
    {0xBE, (uint8_t []){0x00}, 1, 0},
    {0xBF, (uint8_t []){0x00}, 1, 0},
    {0xC0, (uint8_t []){0x10}, 1, 0},
    {0xC1, (uint8_t []){0x47}, 1, 0},
    {0xC2, (uint8_t []){0x56}, 1, 0},
    {0xC3, (uint8_t []){0x65}, 1, 0},
    {0xC4, (uint8_t []){0x74}, 1, 0},
    {0xC5, (uint8_t []){0x88}, 1, 0},
    {0xC6, (uint8_t []){0x99}, 1, 0},
    {0xC7, (uint8_t []){0x01}, 1, 0},
    {0xC8, (uint8_t []){0xBB}, 1, 0},
    {0xC9, (uint8_t []){0xAA}, 1, 0},
    {0xD0, (uint8_t []){0x10}, 1, 0},
    {0xD1, (uint8_t []){0x47}, 1, 0},
    {0xD2, (uint8_t []){0x56}, 1, 0},
    {0xD3, (uint8_t []){0x65}, 1, 0},
    {0xD4, (uint8_t []){0x74}, 1, 0},
    {0xD5, (uint8_t []){0x88}, 1, 0},
    {0xD6, (uint8_t []){0x99}, 1, 0},
    {0xD7, (uint8_t []){0x01}, 1, 0},
    {0xD8, (uint8_t []){0xBB}, 1, 0},
    {0xD9, (uint8_t []){0xAA}, 1, 0},
    {0xF3, (uint8_t []){0x01}, 1, 0},
    {0xF0, (uint8_t []){0x00}, 1, 0},
    {0x21, (uint8_t []){}, 0, 0},
    {0x11, (uint8_t []){}, 0, 0},
    {0x00, (uint8_t []){}, 0, 120},
};
float tsens_value;
gpio_num_t AUDIO_I2S_GPIO_DIN = AUDIO_I2S_GPIO_DIN_1;
gpio_num_t AUDIO_CODEC_PA_PIN = AUDIO_CODEC_PA_PIN_1;
gpio_num_t QSPI_PIN_NUM_LCD_RST = QSPI_PIN_NUM_LCD_RST_1;
gpio_num_t TOUCH_PAD2 = TOUCH_PAD2_1;
gpio_num_t UART1_TX = UART1_TX_1;  // V1.0: GPIO6, verified against base RX20
gpio_num_t UART1_RX = UART1_RX_1;  // V1.0: GPIO5

class Charge : public I2cDevice {
public:
    Charge(i2c_master_bus_handle_t i2c_bus, uint8_t addr) : I2cDevice(i2c_bus, addr)
    {
        read_buffer_ = new uint8_t[8];
    }
    ~Charge()
    {
        delete[] read_buffer_;
    }
    void Printcharge()
    {
        ReadRegs(0x08, read_buffer_, 2);
        ReadRegs(0x0c, read_buffer_ + 2, 2);
        ESP_ERROR_CHECK(temperature_sensor_get_celsius(temp_sensor, &tsens_value));

        int16_t voltage = static_cast<uint16_t>(read_buffer_[1] << 8 | read_buffer_[0]);
        int16_t current = static_cast<int16_t>(read_buffer_[3] << 8 | read_buffer_[2]);
        
        // Use the variables to avoid warnings (can be removed if actual implementation uses them)
        (void)voltage;
        (void)current;
    }
    static void TaskFunction(void *pvParameters)
    {
        Charge* charge = static_cast<Charge*>(pvParameters);
        while (true) {
            charge->Printcharge();
            vTaskDelay(pdMS_TO_TICKS(300));
        }
    }

private:
    uint8_t* read_buffer_ = nullptr;
};

class Cst816s : public I2cDevice {
public:
    struct TouchPoint_t {
        int num = 0;
        int x = -1;
        int y = -1;
    };

    enum TouchEvent {
        TOUCH_NONE,
        TOUCH_PRESS,
        TOUCH_RELEASE,
        TOUCH_HOLD
    };

    Cst816s(i2c_master_bus_handle_t i2c_bus, uint8_t addr) : I2cDevice(i2c_bus, addr)
    {
        read_buffer_ = new uint8_t[6];
        was_touched_ = false;
        press_count_ = 0;

        // Create touch interrupt semaphore
        touch_isr_mux_ = xSemaphoreCreateBinary();
        if (touch_isr_mux_ == NULL) {
            ESP_LOGE("EchoEar", "Failed to create touch semaphore");
        }
    }

    ~Cst816s()
    {
        delete[] read_buffer_;

        // Delete semaphore if it exists
        if (touch_isr_mux_ != NULL) {
            vSemaphoreDelete(touch_isr_mux_);
            touch_isr_mux_ = NULL;
        }
    }

    void UpdateTouchPoint()
    {
        ReadRegs(0x02, read_buffer_, 6);
        tp_.num = read_buffer_[0] & 0x0F;
        tp_.x = ((read_buffer_[1] & 0x0F) << 8) | read_buffer_[2];
        tp_.y = ((read_buffer_[3] & 0x0F) << 8) | read_buffer_[4];
    }

    const TouchPoint_t &GetTouchPoint()
    {
        return tp_;
    }

    TouchEvent CheckTouchEvent()
    {
        bool is_touched = (tp_.num > 0);
        TouchEvent event = TOUCH_NONE;

        if (is_touched && !was_touched_) {
            // Press event (transition from not touched to touched)
            press_count_++;
            event = TOUCH_PRESS;
            ESP_LOGI("EchoEar", "TOUCH PRESS - count: %d, x: %d, y: %d", press_count_, tp_.x, tp_.y);
        } else if (!is_touched && was_touched_) {
            // Release event (transition from touched to not touched)
            event = TOUCH_RELEASE;
            ESP_LOGI("EchoEar", "TOUCH RELEASE - total presses: %d", press_count_);
        } else if (is_touched && was_touched_) {
            // Continuous touch (hold)
            event = TOUCH_HOLD;
            ESP_LOGD("EchoEar", "TOUCH HOLD - x: %d, y: %d", tp_.x, tp_.y);
        }

        // Update previous state
        was_touched_ = is_touched;
        return event;
    }

    int GetPressCount() const
    {
        return press_count_;
    }

    void ResetPressCount()
    {
        press_count_ = 0;
    }

    // Semaphore management methods
    SemaphoreHandle_t GetTouchSemaphore()
    {
        return touch_isr_mux_;
    }

    bool WaitForTouchEvent(TickType_t timeout = portMAX_DELAY)
    {
        if (touch_isr_mux_ != NULL) {
            return xSemaphoreTake(touch_isr_mux_, timeout) == pdTRUE;
        }
        return false;
    }

    void NotifyTouchEvent()
    {
        if (touch_isr_mux_ != NULL) {
            BaseType_t xHigherPriorityTaskWoken = pdFALSE;
            xSemaphoreGiveFromISR(touch_isr_mux_, &xHigherPriorityTaskWoken);
            portYIELD_FROM_ISR(xHigherPriorityTaskWoken);
        }
    }

private:
    uint8_t* read_buffer_ = nullptr;
    TouchPoint_t tp_;

    // Touch state tracking
    bool was_touched_;
    int press_count_;

    // Touch interrupt semaphore
    SemaphoreHandle_t touch_isr_mux_;
};

static bool gBaseCWPositive = true;

class RotatingBaseController {
public:
    RotatingBaseController()
    {
        uart_config_t uart_config = {
            .baud_rate = 115200,
            .data_bits = UART_DATA_8_BITS,
            .parity = UART_PARITY_DISABLE,
            .stop_bits = UART_STOP_BITS_1,
            .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
            .rx_flow_ctrl_thresh = 0,
            .source_clk = UART_SCLK_DEFAULT
        };
        uart_driver_install(UART_NUM_1, 1024, 1024, 0, NULL, 0);
        uart_param_config(UART_NUM_1, &uart_config);
        uart_set_pin(UART_NUM_1, UART1_TX, UART1_RX, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
        ESP_LOGI(TAG, "Base UART init TX=%d RX=%d baud=%d", (int)UART1_TX, (int)UART1_RX, uart_config.baud_rate);
    }
    ~RotatingBaseController()
    {
        uart_driver_delete(UART_NUM_1);
    }

    static RotatingBaseController& GetInstance() {
        static RotatingBaseController instance;
        return instance;
    }

    void RotateToAzimuth(int angle)
    {
        RotateByDelta(std::clamp(angle, -25, 25));
    }

    bool RotateByDelta(int delta)
    {
        PollStatus();
        const int64_t now = esp_timer_get_time() / 1000;
        if (!Ready(now)) return false;
        int mapped = echoear::RelativeTarget(s_curr_mapped_, delta, gBaseCWPositive);
        if (std::abs(mapped-s_curr_mapped_) < 3) return false;
        SendFrame(mapped);
        awaiting_motion_ = true;
        sent_at_ = now;
        ESP_LOGI(TAG, "Base feedback actual=%d command_delta=%d target=%d", s_curr_mapped_, delta, mapped);
        return true;
    }

    void StopMotion() {
        const int64_t now=esp_timer_get_time()/1000;
        if (feedback_at_<=0 || (!moving_ && !awaiting_motion_) || now-stop_sent_at_<200) return;
        const uint8_t stop[]={0xAA,0x55,0,1,0x11,0x11};
        uart_write_bytes(UART_NUM_1,stop,sizeof(stop));
        stop_sent_at_=now;
    }

    void PollStatus() {
        size_t buffered=0;
        uart_get_buffered_data_len(UART_NUM_1,&buffered);
        if (buffered>256) {
            uart_flush_input(UART_NUM_1);
            feedback_at_=0;
            return; // Do not label old queued positions as fresh feedback.
        }
        uint8_t input[256];
        int n = uart_read_bytes(UART_NUM_1, input, sizeof(input), 0);
        const int64_t now = esp_timer_get_time()/1000;
        for (int i=0; i<n; ++i) {
            if (!parser_.Feed(input[i])) continue;
            const auto& f=parser_.bytes;
            if (f[3]!=5 || f[4]!=0x81) continue;
            const int value=(f[5]<<8)|f[6];
            if (value>3000) continue;
            const bool busy=f[7]!=0;
            if (busy || moving_) stable_since_=now;
            moving_=busy;
            fault_=f[8];
            s_curr_mapped_=static_cast<int>(std::lround(value/10.0f));
            feedback_at_=now;
            g_base_feedback_ms.store(now);
            g_base_actual.store(s_curr_mapped_);
            g_base_fault.store(fault_);
            g_base_moving.store(moving_);
            // Ignore a status generated before the command reached the base.
            if (now-sent_at_>=180 || busy) awaiting_motion_=false;
        }
    }
    bool Ready(int64_t now) const {
        return feedback_at_>0 && now-feedback_at_<350 && fault_==0 && !moving_ &&
            !awaiting_motion_ && now-stable_since_>=200;
    }

    void RotateToAbsoluteAzimuth(int angle)
    {
        PollStatus();
        if (!Ready(esp_timer_get_time()/1000)) return;
        const int target=std::clamp(162+(gBaseCWPositive?angle:-angle),77,247);
        RotateByDelta(gBaseCWPositive?target-s_curr_mapped_:s_curr_mapped_-target);
    }

    int GetCurrentMapped() const { return s_curr_mapped_; }
    void SetDirectionPositive(bool v) { gBaseCWPositive = v; }

private:
    void SendFrame(int mapped360) {
        mapped360 = std::clamp(mapped360, 77, 247);
        
        uart_set_baudrate(UART_NUM_1, 115200);
        uint8_t frame[8];
        frame[0] = 0xAA;
        frame[1] = 0x55;
        frame[2] = 0x00;
        frame[3] = 0x03;
        frame[4] = 0x01;
        uint16_t v = (uint16_t)mapped360;
        frame[5] = (uint8_t)((v >> 8) & 0xFF);
        frame[6] = (uint8_t)(v & 0xFF);
        frame[7] = (uint8_t)(frame[4] + frame[5] + frame[6]);
        uart_write_bytes(UART_NUM_1, (const char*)frame, 8);
        vTaskDelay(pdMS_TO_TICKS(10));
    }

    miaoban::BaseFrame parser_;
    int64_t feedback_at_=0,stable_since_=0,sent_at_=0,stop_sent_at_=0;
    bool moving_=true,awaiting_motion_=false;
    int fault_=0;
    int s_last_sent_ = -1;
    int s_last_raw_ = 0;
    int64_t s_last_ms_ = 0;
    int s_curr_mapped_ = -1;
};

class SoundSourceLocator {
public:
    SoundSourceLocator(AudioCodec* codec) : codec_(codec)
    {
        pcm_queue_ = xQueueCreate(8, sizeof(PcmFrame));
        configASSERT(pcm_queue_);
        codec_->SetInputObserver([this](const std::vector<int16_t>& pcm) { ObserveInput(pcm); });
        std::string input_format("MM");
        doa_ = afe_doa_create(input_format.c_str(), 16000, 5.0f, 0.03f, 512);
        ESP_LOGI(TAG, "DOA init: sr=%d ch=%d", codec_->input_sample_rate(), codec_->input_channels());
    }
    ~SoundSourceLocator()
    {
        codec_->SetInputObserver(nullptr);
        if (pcm_queue_) vQueueDelete(pcm_queue_);
        if (doa_) afe_doa_destroy(doa_);
    }
    static void TaskEntry(void* arg)
    {
        auto* self = static_cast<SoundSourceLocator*>(arg);
        auto& app = Application::GetInstance();
        auto& base = RotatingBaseController::GetInstance();
        echoear::SpeechTurnGate gate;
        // Classify shared mic PCM locally, even while waiting for the wake phrase.
        auto speech_vad = vad_create(VAD_MODE_0);
        std::array<int16_t, 480> vad_frame{};
        size_t vad_fill = 0;
        bool local_voice = false;
        if (!speech_vad) ESP_LOGE(TAG, "Local speech VAD unavailable; follow disabled");
        // The base homes itself with actual position feedback; never assume centre.
        constexpr size_t window_samples = 512;
        std::vector<int16_t> frame;
        int64_t last_log_ms = 0;
        int empty_reads = 0;
        echoear::SpeechNoiseFloor noise;
        ESP_LOGI(TAG, "DOA tracking: sensitive v2, noise x1.5/floor30/peak90, 9 stable frames, 64ms gap, feedback required, +/-85deg");
        while (true) {
            base.PollStatus();
            if (!self->ReadObservedFrame(frame, 160)) {
                gate.Observe(0, false, false, esp_timer_get_time() / 1000);
                vad_fill = 0;
                local_voice = false;
                if (++empty_reads % 50 == 0) ESP_LOGW(TAG, "DOA waiting for fresh audio");
                continue;
            }
            empty_reads = 0;
            self->doa_buf_.insert(self->doa_buf_.end(), frame.begin(), frame.end());
            while (self->doa_buf_.size() >= window_samples * 2) {
                int peak_l = 0, peak_r = 0;
                int64_t squares = 0;
                size_t identical = 0;
                for (size_t i = 0; i < window_samples * 2; i += 2) {
                    squares += static_cast<int64_t>(self->doa_buf_[i]) * self->doa_buf_[i];
                    peak_l = std::max(peak_l, std::abs(static_cast<int>(self->doa_buf_[i])));
                    peak_r = std::max(peak_r, std::abs(static_cast<int>(self->doa_buf_[i + 1])));
                    if (self->doa_buf_[i] == self->doa_buf_[i + 1]) ++identical;
                    vad_frame[vad_fill++] = self->doa_buf_[i];
                    if (vad_fill == vad_frame.size()) {
                        local_voice = speech_vad && vad_process(speech_vad, vad_frame.data(), 16000, 30) == VAD_SPEECH;
                        vad_fill = 0;
                    }
                }
                const float azimuth = afe_doa_process(self->doa_, self->doa_buf_.data());
                self->doa_buf_.erase(self->doa_buf_.begin(), self->doa_buf_.begin() + window_samples * 2);
                const int64_t now_ms = esp_timer_get_time() / 1000;
                const auto state = app.GetDeviceState();
                base.PollStatus();
                if (state==kDeviceStateSpeaking || app.GetAudioService().IsOutputActive() || !g_tracking_enabled.load())
                    base.StopMotion();
                const bool listening = g_tracking_enabled.load() &&
                    (state == kDeviceStateIdle || state == kDeviceStateListening) &&
                    !app.GetAudioService().IsOutputActive() && base.Ready(now_ms);
                const bool valid = std::isfinite(azimuth) && azimuth >= 0 && azimuth <= 180 &&
                    identical < window_samples / 2;
                const bool voice = local_voice;
                // DOA uses phase difference; similar left/right loudness is valid.
                const int delta = valid ? static_cast<int>(std::lround(azimuth-g_front_angle.load())) : 0;
                if (valid) g_doa_raw.store(static_cast<int>(std::lround(azimuth)));
                const double rms = std::sqrt(static_cast<double>(squares) / window_samples);
                if (base.Ready(now_ms) && !app.GetAudioService().IsOutputActive()) noise.Observe(rms,voice);
                // Adapt to room noise instead of requiring the user to shout.
                // No motion at all while the robot is playing its own voice.
                const bool speech = listening && valid && noise.Accepts(rms, std::max(peak_l, peak_r), voice);
                if (speech) {
                    g_doa_last_angle = delta;
                    g_doa_last_ms = now_ms;
                }
                const int command = gate.Observe(delta, speech, listening, now_ms);
                if (command != 0 && base.RotateByDelta(command)) {
                    ESP_LOGI(TAG, "DOA follow raw=%.1f delta=%d command=%d actual_before=%d",
                        azimuth, delta, command, base.GetCurrentMapped());
                }
                // Keep enough speech samples to distinguish a bad estimate
                // from a motor tracking error during fixed-position trials.
                if (now_ms - last_log_ms >= ((voice && valid && rms >= noise.Threshold() &&
                    state != kDeviceStateSpeaking && !app.GetAudioService().IsOutputActive()) ? 100 : 2000)) {
                    ESP_LOGI(TAG, "DOA state=%d vad=%d valid=%d L=%d R=%d raw=%.1f delta=%d rms=%d gate=%d ready=%d",
                        static_cast<int>(app.GetDeviceState()), voice, valid, peak_l, peak_r, azimuth, delta,
                        static_cast<int>(rms),static_cast<int>(noise.Threshold()),base.Ready(now_ms));
                    last_log_ms = now_ms;
                }
            }
        }
    }

private:
    struct PcmFrame {
        std::array<int16_t, 320> pcm;
        int64_t captured_us;
        uint32_t sequence;
    };
    QueueHandle_t pcm_queue_ = nullptr;
    PcmFrame pending_{};
    size_t pending_samples_ = 0;
    uint32_t next_sequence_ = 0;
    uint32_t last_sequence_ = 0;
    int64_t last_input_callback_us_ = 0;

    void ObserveInput(const std::vector<int16_t>& data) {
        const int64_t now = esp_timer_get_time();
        if (last_input_callback_us_ && now - last_input_callback_us_ > 200000) {
            pending_samples_ = 0;
            ++next_sequence_;
        }
        last_input_callback_us_ = now;
        const int channels = codec_->input_channels();
        const int rate = codec_->input_sample_rate();
        if (channels < 2 || rate < 16000) return;
        const size_t input_frames = data.size() / channels;
        const size_t output_frames = input_frames * 16000 / rate;
        for (size_t i = 0; i < output_frames; ++i) {
            const double pos = static_cast<double>(i) * rate / 16000.0;
            const size_t a = static_cast<size_t>(pos);
            const size_t b = std::min(a + 1, input_frames - 1);
            const double f = pos - a;
            for (int ch = 0; ch < 2; ++ch) {
                pending_.pcm[pending_samples_ * 2 + ch] = static_cast<int16_t>(
                    data[a * channels + ch] * (1 - f) + data[b * channels + ch] * f);
            }
            if (++pending_samples_ == 160) {
                pending_.captured_us = esp_timer_get_time();
                pending_.sequence = ++next_sequence_;
                if (xQueueSend(pcm_queue_, &pending_, 0) != pdTRUE) {
                    PcmFrame discarded;
                    xQueueReceive(pcm_queue_, &discarded, 0);
                    xQueueSend(pcm_queue_, &pending_, 0);
                }
                pending_samples_ = 0;
            }
        }
    }

    bool ReadObservedFrame(std::vector<int16_t>& out, size_t samples) {
        PcmFrame frame;
        if (samples != 160 || xQueueReceive(pcm_queue_, &frame, pdMS_TO_TICKS(100)) != pdTRUE) {
            doa_buf_.clear();
            return false;
        }
        if (esp_timer_get_time() - frame.captured_us > 200000) {
            doa_buf_.clear();
            return false;
        }
        if (last_sequence_ && frame.sequence != last_sequence_ + 1) doa_buf_.clear();
        last_sequence_ = frame.sequence;
        out.assign(frame.pcm.begin(), frame.pcm.end());
        return true;
    }
    AudioCodec* codec_ = nullptr;
    afe_doa_handle_t* doa_ = nullptr;
    std::vector<int16_t> doa_buf_;
};

class Mpu6050Imu : public I2cDevice {
public:
    Mpu6050Imu(i2c_master_bus_handle_t bus, uint8_t addr) : I2cDevice(bus, addr) {}
    bool Initialize()
    {
        uint8_t who = ReadReg(0x75);
        if (who == 0x68 || who == 0x70) {
            WriteReg(0x6B, 0x00);
            return true;
        }
        return false;
    }
    bool ReadAccel(float& ax, float& ay, float& az)
    {
        uint8_t buf[6];
        ReadRegs(0x3B, buf, 6);
        int16_t x = (int16_t)((buf[0] << 8) | buf[1]);
        int16_t y = (int16_t)((buf[2] << 8) | buf[3]);
        int16_t z = (int16_t)((buf[4] << 8) | buf[5]);
        float scale = 16384.0f;
        ax = (float)x / scale;
        ay = (float)y / scale;
        az = (float)z / scale;
        return true;
    }
};

    class Bma423Imu : public I2cDevice {
public:
    Bma423Imu(i2c_master_bus_handle_t bus, uint8_t addr) : I2cDevice(bus, addr), bus_(bus), addr_(addr) {}
    bool Initialize()
    {
        // Reattach(100000, 0); // Removed unconditional reattach to prevent bus reset loop
        uint8_t chip = 0;
        uint8_t r0 = 0x00;
        for (int i = 0; i < 3; i++) {
            if (i2c_master_transmit_receive(i2c_device_, &r0, 1, &chip, 1, 100) == ESP_OK) {
                if (chip != 0x00 && chip != 0xFF) break;
            }
            vTaskDelay(pdMS_TO_TICKS(10));
        }
        ESP_LOGI("BMA423", "Probing addr 0x%02X, Chip ID: 0x%02X", addr_, chip);
        if (chip != 0x13 && chip != 0x1F) { // Enforce BMA423 Chip ID (allow 0x1F as potential variant)
            ESP_LOGE("BMA423", "Invalid Chip ID (expected 0x13), attempting reattach...");
            Reattach(100000, 0);
            vTaskDelay(pdMS_TO_TICKS(10));
            if (i2c_master_transmit_receive(i2c_device_, &r0, 1, &chip, 1, 100) != ESP_OK || (chip != 0x13 && chip != 0x1F)) {
                ESP_LOGE("BMA423", "Still Invalid Chip ID after reattach: 0x%02X", chip);
                return false;
            }
            ESP_LOGI("BMA423", "Chip ID recovered: 0x%02X", chip);
        }
        
        // Soft reset
        uint8_t w2[2] = {0x7E, 0xB6}; 
        if (i2c_master_transmit(i2c_device_, w2, 2, 100) != ESP_OK) {
             ESP_LOGE("BMA423", "Soft reset tx failed");
             return false;
        }
        vTaskDelay(pdMS_TO_TICKS(20)); // Increased wait for soft reset
        
        uint8_t w3[2] = {0x7C, 0x00}; // Power save disable
        if (i2c_master_transmit(i2c_device_, w3, 2, 100) != ESP_OK) {
            ESP_LOGE("BMA423", "Power save disable failed");
            return false;
        }
        vTaskDelay(pdMS_TO_TICKS(5));
        
        uint8_t w4[2] = {0x40, 0xA8}; // Accel config: ODR 100Hz, Normal, Perf mode
        uint8_t w5[2] = {0x41, 0x00}; // Accel range +/- 2g
        if (i2c_master_transmit(i2c_device_, w4, 2, 100) != ESP_OK) {
             ESP_LOGE("BMA423", "Accel config failed");
             return false;
        }
        if (i2c_master_transmit(i2c_device_, w5, 2, 100) != ESP_OK) {
             ESP_LOGE("BMA423", "Accel range failed");
             return false;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
        
        uint8_t pwr = 0;
        uint8_t rp = 0x7D;
        if (i2c_master_transmit_receive(i2c_device_, &rp, 1, &pwr, 1, 100) == ESP_OK) {
            uint8_t w6[2] = {0x7D, (uint8_t)(pwr | 0x04)}; // Enable Accel
            if (i2c_master_transmit(i2c_device_, w6, 2, 100) != ESP_OK) {
                 ESP_LOGE("BMA423", "Enable accel tx failed");
                 return false;
            }
        } else {
            ESP_LOGE("BMA423", "Failed to read PWR_CTRL");
            return false;
        }
        vTaskDelay(pdMS_TO_TICKS(50));
        
        // Verify enabled
        uint8_t check_pwr = 0;
        i2c_master_transmit_receive(i2c_device_, &rp, 1, &check_pwr, 1, 100);
        if ((check_pwr & 0x04) == 0) {
             ESP_LOGW("BMA423", "Accel enable failed? PWR=0x%02X", check_pwr);
             // Try one more time
             uint8_t w6[2] = {0x7D, 0x04};
             i2c_master_transmit(i2c_device_, w6, 2, 100);
             vTaskDelay(pdMS_TO_TICKS(10));
             i2c_master_transmit_receive(i2c_device_, &rp, 1, &check_pwr, 1, 100);
             if ((check_pwr & 0x04) == 0) {
                 ESP_LOGE("BMA423", "Accel enable retry failed PWR=0x%02X", check_pwr);
                 return false;
             }
        }

        inited_ = true;
        return true;
    }
    bool ReadAccel(float& ax, float& ay, float& az)
    {
        uint8_t buf[6];
        uint8_t reg = 0x12;
        if (i2c_master_transmit_receive(i2c_device_, &reg, 1, buf, 6, 100) != ESP_OK) {
            return false;
        }
        
        // Check for invalid data (all 00 or all FF)
        bool all_zeros = true;
        bool all_ff = true;
        for(int i=0; i<6; i++) {
            if (buf[i] != 0) all_zeros = false;
            if (buf[i] != 0xFF) all_ff = false;
        }
        if (all_zeros || all_ff) return false;

        int16_t x = (int16_t)((buf[1] << 8) | buf[0]);
        int16_t y = (int16_t)((buf[3] << 8) | buf[2]);
        int16_t z = (int16_t)((buf[5] << 8) | buf[4]);

        // BMA423 is 12-bit, left aligned in 16-bit registers if shadowing is enabled?
        // Actually usually it's signed 16-bit where the lower 4 bits are 0.
        // Wait, BMA423 datasheet:
        // Register 0x12: ACC_X_LSB [7:4] = acc_x[3:0]
        // Register 0x13: ACC_X_MSB [7:0] = acc_x[11:4]
        // Value = (MSB * 256 + LSB) / 16
        
        x >>= 4;
        y >>= 4;
        z >>= 4;

        // Range +/- 2g (assuming 0x41=0x00 in Init) -> 1 LSB = 0.98mg
        // 2g range: 1024 LSB/g
        ax = (float)x / 1024.0f;
        ay = (float)y / 1024.0f;
        az = (float)z / 1024.0f;
        
        if (fabsf(ax) > 3.9f || fabsf(ay) > 3.9f || fabsf(az) > 3.9f) return false;
        return true;
    }

    bool EnableAccel(uint8_t acc_conf, uint8_t acc_range)
    {
        if (!inited_) return false;
        // Force 100Hz ODR to ensure data ready
        acc_conf = 0xA8; // 100Hz, Normal mode
        
        uint8_t w1[2] = {0x40, acc_conf};
        uint8_t w2[2] = {0x41, acc_range};
        i2c_master_transmit(i2c_device_, w1, 2, 100);
        i2c_master_transmit(i2c_device_, w2, 2, 100);
        vTaskDelay(pdMS_TO_TICKS(10));
        
        uint8_t pwr = 0;
        uint8_t rp = 0x7D;
        // Read current power mode
        if (i2c_master_transmit_receive(i2c_device_, &rp, 1, &pwr, 1, 100) == ESP_OK) {
            // Enable Accel (bit 2)
            uint8_t w3[2] = {0x7D, (uint8_t)(pwr | 0x04)};
            i2c_master_transmit(i2c_device_, w3, 2, 100);
        } else {
            // Blind write if read fails
            uint8_t w3[2] = {0x7D, 0x04};
            i2c_master_transmit(i2c_device_, w3, 2, 100);
        }
        vTaskDelay(pdMS_TO_TICKS(20));
        return true;
    }
    bool ReadRaw6(uint8_t* out)
    {
        uint8_t r = 0x12;
        if (i2c_master_transmit_receive(i2c_device_, &r, 1, out, 6, 1000) != ESP_OK) return false;
        return true;
    }
    bool Reattach(uint32_t scl_hz, uint16_t scl_wait_us)
    {
        if (i2c_device_) {
            (void)i2c_master_bus_rm_device(i2c_device_);
            i2c_device_ = nullptr;
        }
        i2c_device_config_t cfg = {
            .dev_addr_length = I2C_ADDR_BIT_LEN_7,
            .device_address = addr_,
            .scl_speed_hz = scl_hz,
            .scl_wait_us = scl_wait_us,
            .flags = {.disable_ack_check = 0},
        };
        esp_err_t err = i2c_master_bus_add_device(bus_, &cfg, &i2c_device_);
        vTaskDelay(pdMS_TO_TICKS(2));
        return err == ESP_OK;
    }
private:
    bool inited_ = false;
    i2c_master_bus_handle_t bus_;
    uint8_t addr_;
};

class EspS3Cat : public WifiBoard {
private:
    i2c_master_bus_handle_t i2c_bus_;
    Cst816s* cst816s_;
    Charge* charge_;
    Button boot_button_;
    Display* display_ = nullptr;
    PwmBacklight* backlight_ = nullptr;
    esp_timer_handle_t touchpad_timer_;
    esp_lcd_touch_handle_t tp;   // LCD touch handle
    esp_timer_handle_t doa_start_timer_ = nullptr;
    
    bool doa_enabled_ = true;
    bool imu_diag_isolation_ = false;

    void InitializeI2c()
    {
        i2c_master_bus_config_t i2c_bus_cfg = {
            .i2c_port = I2C_NUM_0,
            .sda_io_num = AUDIO_CODEC_I2C_SDA_PIN,
            .scl_io_num = AUDIO_CODEC_I2C_SCL_PIN,
            .clk_source = I2C_CLK_SRC_DEFAULT,
            .glitch_ignore_cnt = 7,
            .intr_priority = 0,
            .trans_queue_depth = 0,
            .flags = {
                .enable_internal_pullup = 1,
            },
        };
        ESP_ERROR_CHECK(i2c_new_master_bus(&i2c_bus_cfg, &i2c_bus_));

        temperature_sensor_config_t temp_sensor_config = TEMPERATURE_SENSOR_CONFIG_DEFAULT(10, 50);
        ESP_ERROR_CHECK(temperature_sensor_install(&temp_sensor_config, &temp_sensor));
        ESP_ERROR_CHECK(temperature_sensor_enable(temp_sensor));

    }
    void RecoverI2cBus()
    {
        i2c_del_master_bus(i2c_bus_);
        gpio_config_t cfg;
        cfg.pin_bit_mask = (1ULL << AUDIO_CODEC_I2C_SCL_PIN) | (1ULL << AUDIO_CODEC_I2C_SDA_PIN);
        cfg.mode = GPIO_MODE_OUTPUT_OD;
        cfg.pull_up_en = GPIO_PULLUP_ENABLE;
        cfg.pull_down_en = GPIO_PULLDOWN_DISABLE;
        cfg.intr_type = GPIO_INTR_DISABLE;
        gpio_config(&cfg);
        gpio_set_level(AUDIO_CODEC_I2C_SCL_PIN, 1);
        gpio_set_level(AUDIO_CODEC_I2C_SDA_PIN, 1);
        vTaskDelay(pdMS_TO_TICKS(2));
        for (int i = 0; i < 16; ++i) {
            gpio_set_level(AUDIO_CODEC_I2C_SCL_PIN, 0);
            vTaskDelay(pdMS_TO_TICKS(1));
            gpio_set_level(AUDIO_CODEC_I2C_SCL_PIN, 1);
            vTaskDelay(pdMS_TO_TICKS(1));
        }
        gpio_set_level(AUDIO_CODEC_I2C_SDA_PIN, 0);
        vTaskDelay(pdMS_TO_TICKS(1));
        gpio_set_level(AUDIO_CODEC_I2C_SCL_PIN, 1);
        vTaskDelay(pdMS_TO_TICKS(1));
        gpio_set_level(AUDIO_CODEC_I2C_SDA_PIN, 1);
        vTaskDelay(pdMS_TO_TICKS(2));
        i2c_master_bus_config_t i2c_bus_cfg = {
            .i2c_port = I2C_NUM_0,
            .sda_io_num = AUDIO_CODEC_I2C_SDA_PIN,
            .scl_io_num = AUDIO_CODEC_I2C_SCL_PIN,
            .clk_source = I2C_CLK_SRC_DEFAULT,
            .glitch_ignore_cnt = 7,
            .intr_priority = 0,
            .trans_queue_depth = 0,
            .flags = {
                .enable_internal_pullup = 1,
            },
        };
        ESP_ERROR_CHECK(i2c_new_master_bus(&i2c_bus_cfg, &i2c_bus_));
    }

    void PowerResetImu()
    {
        gpio_set_level(POWER_CTRL, 1);
        vTaskDelay(pdMS_TO_TICKS(150));
        gpio_set_level(POWER_CTRL, 0);
        vTaskDelay(pdMS_TO_TICKS(200));
    }

    uint8_t DetectPcbVersion()
    {
        esp_err_t ret = i2c_master_probe(i2c_bus_, 0x18, 100);
        uint8_t pcb_verison = 0;
        if (ret == ESP_OK) {
            ESP_LOGI(TAG, "PCB verison V1.0");
            pcb_verison = 0;
        } else {
            gpio_config_t gpio_conf = {
                .pin_bit_mask = (1ULL << GPIO_NUM_48),
                .mode = GPIO_MODE_OUTPUT,
                .pull_up_en = GPIO_PULLUP_DISABLE,
                .pull_down_en = GPIO_PULLDOWN_DISABLE,
                .intr_type = GPIO_INTR_DISABLE
            };
            ESP_ERROR_CHECK(gpio_config(&gpio_conf));
            ESP_ERROR_CHECK(gpio_set_level(GPIO_NUM_48, 1));
            vTaskDelay(pdMS_TO_TICKS(100));
            ret = i2c_master_probe(i2c_bus_, 0x18, 100);
            if (ret == ESP_OK) {
                ESP_LOGI(TAG, "PCB verison V1.2");
                pcb_verison = 1;
                AUDIO_I2S_GPIO_DIN = AUDIO_I2S_GPIO_DIN_2;
                AUDIO_CODEC_PA_PIN = AUDIO_CODEC_PA_PIN_2;
                QSPI_PIN_NUM_LCD_RST = QSPI_PIN_NUM_LCD_RST_2;
                TOUCH_PAD2 = TOUCH_PAD2_2;
                UART1_TX = UART1_TX_2;
                UART1_RX = UART1_RX_2;
            } else {
                ESP_LOGE(TAG, "PCB version detection error");

            }
        }
        return pcb_verison;
    }

    static void touch_isr_callback(void* arg)
    {
        Cst816s* touchpad = static_cast<Cst816s*>(arg);
        if (touchpad != nullptr) {
            touchpad->NotifyTouchEvent();
        }
    }

    static void touch_event_task(void* arg)
    {
        Cst816s* touchpad = static_cast<Cst816s*>(arg);
        if (touchpad == nullptr) {
            ESP_LOGE(TAG, "Invalid touchpad pointer in touch_event_task");
            vTaskDelete(NULL);
            return;
        }

        while (true) {
            if (touchpad->WaitForTouchEvent()) {
                auto &app = Application::GetInstance();
                auto &board = (EspS3Cat &)Board::GetInstance();

                ESP_LOGI(TAG, "Touch event, TP_PIN_NUM_INT: %d", gpio_get_level(TP_PIN_NUM_INT));
                touchpad->UpdateTouchPoint();
                auto touch_event = touchpad->CheckTouchEvent();

                if (touch_event == Cst816s::TOUCH_RELEASE) {
                    if (app.GetDeviceState() == kDeviceStateStarting &&
                            !WifiStation::GetInstance().IsConnected()) {
                        board.ResetWifiConfiguration();
                    } else {
                        app.ToggleChatState();
                    }
                }
            }
        }
    }

    void InitializeCharge()
    {
        esp_err_t ret = i2c_master_probe(i2c_bus_, 0x55, 100);
        if (ret == ESP_OK) {
            charge_ = new Charge(i2c_bus_, 0x55);
            xTaskCreatePinnedToCore(Charge::TaskFunction, "batterydecTask", 3 * 1024, charge_, 6, NULL, 0);
        } else {
            ESP_LOGW(TAG, "Charge IC not found on I2C addr 0x55, skip charge task");
        }
    }

    void InitializeCst816sTouchPad()
    {
        cst816s_ = new Cst816s(i2c_bus_, 0x15);

        xTaskCreatePinnedToCore(touch_event_task, "touch_task", 4 * 1024, cst816s_, 5, NULL, 1);

        const gpio_config_t int_gpio_config = {
            .pin_bit_mask = (1ULL << TP_PIN_NUM_INT),
            .mode = GPIO_MODE_INPUT,
            // .intr_type = GPIO_INTR_NEGEDGE
            .intr_type = GPIO_INTR_ANYEDGE
        };
        gpio_config(&int_gpio_config);
        gpio_install_isr_service(0);
        gpio_intr_enable(TP_PIN_NUM_INT);
        gpio_isr_handler_add(TP_PIN_NUM_INT, EspS3Cat::touch_isr_callback, cst816s_);
    }

    void InitializeSpi()
    {
        const spi_bus_config_t bus_config = TAIJIPI_ST77916_PANEL_BUS_QSPI_CONFIG(QSPI_PIN_NUM_LCD_PCLK,
                                                                                  QSPI_PIN_NUM_LCD_DATA0,
                                                                                  QSPI_PIN_NUM_LCD_DATA1,
                                                                                  QSPI_PIN_NUM_LCD_DATA2,
                                                                                  QSPI_PIN_NUM_LCD_DATA3,
                                                                                  QSPI_LCD_H_RES * 80 * sizeof(uint16_t));
        ESP_ERROR_CHECK(spi_bus_initialize(QSPI_LCD_HOST, &bus_config, SPI_DMA_CH_AUTO));
    }

    void Initializest77916Display(uint8_t pcb_verison)
    {

        esp_lcd_panel_io_handle_t panel_io = nullptr;
        esp_lcd_panel_handle_t panel = nullptr;

        const esp_lcd_panel_io_spi_config_t io_config = ST77916_PANEL_IO_QSPI_CONFIG(QSPI_PIN_NUM_LCD_CS, NULL, NULL);
        ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)QSPI_LCD_HOST, &io_config, &panel_io));
        st77916_vendor_config_t vendor_config = {
            .init_cmds = vendor_specific_init_yysj,
            .init_cmds_size = sizeof(vendor_specific_init_yysj) / sizeof(st77916_lcd_init_cmd_t),
            .flags = {
                .use_qspi_interface = 1,
            },
        };
        const esp_lcd_panel_dev_config_t panel_config = {
            .reset_gpio_num = QSPI_PIN_NUM_LCD_RST,
            .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB,
            .bits_per_pixel = QSPI_LCD_BIT_PER_PIXEL,
            .flags = {
                .reset_active_high = pcb_verison,
            },
            .vendor_config = &vendor_config,
        };
        ESP_ERROR_CHECK(esp_lcd_new_panel_st77916(panel_io, &panel_config, &panel));

        esp_lcd_panel_reset(panel);
        esp_lcd_panel_init(panel);
        esp_lcd_panel_disp_on_off(panel, true);
        esp_lcd_panel_swap_xy(panel, DISPLAY_SWAP_XY);
        esp_lcd_panel_mirror(panel, DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y);

#if CONFIG_USE_EMOTE_MESSAGE_STYLE
        display_ = new emote::EmoteDisplay(panel, panel_io, DISPLAY_WIDTH, DISPLAY_HEIGHT);
#else
        display_ = new SpiLcdDisplay(panel_io, panel,
            DISPLAY_WIDTH, DISPLAY_HEIGHT, DISPLAY_OFFSET_X, DISPLAY_OFFSET_Y, DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y, DISPLAY_SWAP_XY);
#endif
        backlight_ = new PwmBacklight(DISPLAY_BACKLIGHT_PIN, DISPLAY_BACKLIGHT_OUTPUT_INVERT);
        backlight_->RestoreBrightness();
    }

    void InitializeButtons()
    {
        boot_button_.OnClick([this]() {
            auto &app = Application::GetInstance();
            if (app.GetDeviceState() == kDeviceStateStarting && !WifiStation::GetInstance().IsConnected()) {
                ESP_LOGI(TAG, "Boot button pressed, enter WiFi configuration mode");
                ResetWifiConfiguration();
            }
            app.ToggleChatState();
        });
        
        gpio_config_t power_gpio_config = {
            .pin_bit_mask = (BIT64(POWER_CTRL)),
            .mode = GPIO_MODE_OUTPUT,

        };
        ESP_ERROR_CHECK(gpio_config(&power_gpio_config));

        gpio_set_level(POWER_CTRL, 0);
    }

    static void imu_task(void* arg)
    {
        // Delay IMU task to avoid I2C conflict during system startup (AudioCodec init, etc.)
        vTaskDelay(pdMS_TO_TICKS(5000));

        EspS3Cat* self = static_cast<EspS3Cat*>(arg);
        // Bma423Imu bma18(self->i2c_bus_, 0x18); // 0x18 is ES8311 on this board
        Bma423Imu bma19(self->i2c_bus_, 0x19);
        // Bma423Imu bma15(self->i2c_bus_, 0x15); // Occupied by CST816S Touch
        Bma423Imu* bma = nullptr;
        Mpu6050Imu mpu(self->i2c_bus_, 0x68);
        BMI270Imu bmi270(self->i2c_bus_, 0x68);
        bool use_bma = false;
        uint8_t bma_addr = 0x00;
        bool ok = false;
        bool bmi270_present = false;
        bool bma_present = false;
        int bmi_zero_trials = 0;
        int64_t s_boot_ms = esp_timer_get_time()/1000;
        static int s_fail_log_cnt = 0;
        bool s_bmi_raw_valid = false;
        // gpio_set_level(POWER_CTRL, 1);
        // vTaskDelay(pdMS_TO_TICKS(20));
        // gpio_set_level(POWER_CTRL, 0);
        // vTaskDelay(pdMS_TO_TICKS(20));
        // I2C scan removed to reduce bus contention during boot
        if (i2c_master_probe(self->i2c_bus_, 0x68, 100) == ESP_OK) {
            bmi270.SetAddress(0x68);
            bool init_ok = bmi270.Initialize();
            uint8_t chip = 0; bmi270.ReadRaw(0x00, &chip, 1);
            const char* cname = (chip == 0x24 ? "BMI270" : (chip == 0x26 ? "BMI26x" : "BMI2"));
            ESP_LOGI(TAG, "IMU device: %s addr=0x68 id=0x%02X", cname, chip);
            use_bma = false; ok = init_ok; bmi270_present = init_ok;
            if (init_ok) s_bmi_raw_valid = true;
            if (!init_ok) {
                if (mpu.Initialize()) {
                    ESP_LOGW(TAG, "BMI init failed, fallback to MPU6050 (0x68)");
                    ok = true; bmi270_present = false; bma_present = false;
                } else if (i2c_master_probe(self->i2c_bus_, 0x19, 100) == ESP_OK && bma19.Initialize()) {
                    ESP_LOGW(TAG, "BMI init failed, fallback to BMA423 (0x19)");
                    bma = &bma19; bma->EnableAccel(0xA8, 0x00);
                    bma_present = true; ok = true; bmi270_present = false;
                }
            }
        } else if (i2c_master_probe(self->i2c_bus_, 0x69, 100) == ESP_OK) {
            bmi270.SetAddress(0x69);
            bool init_ok = bmi270.Initialize();
            uint8_t chip2 = 0; bmi270.ReadRaw(0x00, &chip2, 1);
            const char* cname2 = (chip2 == 0x24 ? "BMI270" : (chip2 == 0x26 ? "BMI26x" : "BMI2"));
            ESP_LOGI(TAG, "IMU device: %s addr=0x69 id=0x%02X", cname2, chip2);
            use_bma = false; ok = init_ok; bmi270_present = init_ok;
            if (init_ok) s_bmi_raw_valid = true;
            if (!init_ok) {
                if (mpu.Initialize()) {
                    ESP_LOGW(TAG, "BMI init failed, fallback to MPU6050 (0x69)");
                    ok = true; bmi270_present = false; bma_present = false;
                } else if (i2c_master_probe(self->i2c_bus_, 0x19, 100) == ESP_OK) {
                    if (bma19.Initialize()) {
                        ESP_LOGW(TAG, "BMI init failed, fallback to BMA423 (0x19)");
                        bma = &bma19; bma_addr = 0x19;
                        bma->EnableAccel(0xA8, 0x00);
                        bma_present = true; ok = true; bmi270_present = false;
                    }
                }
            }
        } else if (i2c_master_probe(self->i2c_bus_, 0x19, 100) == ESP_OK) {
            if (bma19.Initialize()) {
                ESP_LOGI(TAG, "IMU device: BMA423 addr=0x19");
                bma = &bma19; bma_addr = 0x19;
                bma_present = true; ok = true; bmi270_present = false;
            }
        }
        auto display = Board::GetInstance().GetDisplay();
        int last_emote_time = 0;
        int last_emote_code = 0;
        float prev_ax = 0, prev_ay = 0, prev_az = 0;
        float avg_ax = 0.0f, avg_ay = 0.0f, avg_az = 0.0f;
        bool s_filter_init = false;
        float prev_mag = 0;
        int shake_score = 0;
        
        int64_t last_dizzy_ms = 0;
        int64_t last_imu_log_ms = 0;
        bool imu_selected_logged = false;
        int64_t imu_start_ms = esp_timer_get_time()/1000;
        int64_t last_retry_log_ms = 0;
        static int64_t s_last_bmi_init_ms = 0;
        static int s_bmi_init_attempts = 0;
        static bool s_mpu_inited = false;
        static int64_t s_i2c_quiet_until_ms = 0;
        static int64_t s_next_bmi_try_ms = 0;
        static int s_bmi_backoff_stage = 0;
        static int64_t s_imu_status_log_ms = 0;
        static int64_t s_coherence_quiet_until_ms = 0;
            auto do_imu_hard_reset = [&]() {
                ESP_LOGW(TAG, "IMU hard reset: toggling POWER_CTRL and scheduling quiet window");
                gpio_set_level(POWER_CTRL, 1);
                vTaskDelay(pdMS_TO_TICKS(20));
                gpio_set_level(POWER_CTRL, 0);
                vTaskDelay(pdMS_TO_TICKS(50));
                int64_t now_ms = esp_timer_get_time()/1000;
                s_i2c_quiet_until_ms = now_ms + 30000;
                bmi270.SetDriverQuiet(now_ms + 30000);
                s_next_bmi_try_ms = now_ms + 500;
                s_bmi_backoff_stage = 0;
                s_bmi_init_attempts = 0;
                s_bmi_raw_valid = false;
                bmi270_present = false;
            };
        int stuck_count = 0;
        while (true) {
            if (!ok) {
                int64_t now_retry = esp_timer_get_time()/1000;
                if (now_retry - last_retry_log_ms > 30000) { ESP_LOGD(TAG, "IMU not initialized, retrying..."); last_retry_log_ms = now_retry; }
                vTaskDelay(pdMS_TO_TICKS(500));
                int64_t now_gate = esp_timer_get_time()/1000;
                if (s_i2c_quiet_until_ms > now_gate) {
                    ok = false;
                } else {
                    bool tried = false;
                    if (i2c_master_probe(self->i2c_bus_, 0x68, 100) == ESP_OK) {
                        bmi270.SetAddress(0x68);
                        tried = true;
                        if (bmi270.Initialize()) {
                            ESP_LOGI(TAG, "IMU device: BMI270/BMI26x addr=0x68 (forced)");
                            use_bma = false; ok = true; bmi270_present = true;
                        } else {
                            ESP_LOGD(TAG, "EchoEar: BMI270 Init failed at addr 0x68");
                            ok = false;
                        }
                    }
                    if (!ok && i2c_master_probe(self->i2c_bus_, 0x69, 100) == ESP_OK) {
                        bmi270.SetAddress(0x69);
                        tried = true;
                        if (bmi270.Initialize()) {
                            ESP_LOGI(TAG, "IMU device: BMI270/BMI26x addr=0x69 (forced)");
                            use_bma = false; ok = true; bmi270_present = true;
                        } else {
                            ESP_LOGD(TAG, "EchoEar: BMI270 Init failed at addr 0x69");
                            ok = false;
                        }
                    }
                    if (!tried) ok = false;
                }
                if (!ok && bmi270_present) { if (now_retry - last_retry_log_ms > 30000) { ESP_LOGD(TAG, "IMU BMI270 present but not enabled, retrying"); last_retry_log_ms = now_retry; } }
                if (!ok) {
                    int64_t now_stat_ms = esp_timer_get_time()/1000;
                    if (now_stat_ms - s_imu_status_log_ms > 1000) {
                        int quiet_ms = (s_i2c_quiet_until_ms > now_stat_ms) ? (int)(s_i2c_quiet_until_ms - now_stat_ms) : 0;
                        ESP_LOGI(TAG, "IMU status: present(bmi=%d bma=%d) read_ok=0 quiet_remain=%dms zero_trials=%d raw_valid=%d",
                            bmi270_present, bma_present, quiet_ms, bmi_zero_trials, (int)s_bmi_raw_valid);
                        s_imu_status_log_ms = now_stat_ms;
                        static int64_t s_next_diag_ms = 0;
                if (quiet_ms == 0 && (s_next_diag_ms == 0 || now_stat_ms >= s_next_diag_ms)) {
                    uint8_t who = 0;
                    bmi270.Reattach(50000, 12);
                    if (bmi270.ReadRaw(0x00, &who, 1)) {
                        ESP_LOGI(TAG, "IMU diag WHOAMI: 0x%02X", who);
                        if (who == 0x26) {
                            uint8_t rp=0, er=0, stx=0;
                            (void)bmi270.ReadRaw(0x7D, &rp, 1);
                            (void)bmi270.ReadRaw(0x02, &er, 1);
                            (void)bmi270.ReadRaw(0x1B, &stx, 1);
                            ESP_LOGI(TAG, "IMU diag REGS PWR=0x%02X ERR=0x%02X STAT=0x%02X", rp, er, stx);
                            bool recovered = bmi270.TryRecoverData();
                            if (!recovered) {
                                bmi270.Initialize();
                                bmi270.EnsureEnabled();
                            }
                                    int drdy = 0;
                                    int drdy_int0 = 0;
                                    for (int k = 0; k < 10; ++k) {
                                        uint8_t st = 0;
                                        if (who == 0x26) {
                                            if (bmi270.ReadRaw(0x1C, &st, 1) && (st & 0xC0)) { drdy_int0 = 1; break; }
                                        } else {
                                            if (bmi270.ReadRaw(0x1B, &st, 1) && (st & 0xC0)) { drdy = 1; break; }
                                        }
                                        vTaskDelay(pdMS_TO_TICKS(50));
                                    }
                                    if (who == 0x26) ESP_LOGI(TAG, "IMU diag DRDY_INT0=%d", drdy_int0);
                                    else ESP_LOGI(TAG, "IMU diag DRDY=%d", drdy);
                                    if (!drdy) {
                                        int lodr_drdy = 0;
                                        bmi270.EnableAccel(0xA4, 0x00);
                                        vTaskDelay(pdMS_TO_TICKS(80));
                                        for (int k = 0; k < 10; ++k) {
                                            uint8_t st3 = 0;
                                            if (bmi270.ReadRaw(0x1B, &st3, 1) && (st3 & 0xC0)) { lodr_drdy = 1; break; }
                                            vTaskDelay(pdMS_TO_TICKS(50));
                                        }
                                        ESP_LOGI(TAG, "IMU diag LODR_DRDY=%d", lodr_drdy);
                                        if (lodr_drdy) {
                                            bmi270.EnableAccel(0xA8, 0x00);
                                            vTaskDelay(pdMS_TO_TICKS(80));
                                            bmi270_present = true;
                                            ok = true;
                                        }
                                        if (!lodr_drdy) {
                                            int rec_drdy = 0;
                                            bmi270.Reattach(25000, 20);
                                            bmi270.Initialize();
                                            bmi270.EnsureEnabled();
                                            for (int k = 0; k < 20; ++k) {
                                                uint8_t st4 = 0;
                                                if (bmi270.ReadRaw(0x1B, &st4, 1) && (st4 & 0xC0)) { rec_drdy = 1; break; }
                                                vTaskDelay(pdMS_TO_TICKS(60));
                                            }
                                            ESP_LOGI(TAG, "IMU diag RECOVERY_DRDY=%d", rec_drdy);
                                            if (rec_drdy) { bmi270_present = true; ok = true; } else {
                                                self->PowerResetImu();
                                                int pd_drdy = 0;
                                                bmi270.Reattach(50000, 12);
                                                bmi270.Initialize();
                                                bmi270.EnsureEnabled();
                                                for (int k = 0; k < 30; ++k) {
                                                    uint8_t st5 = 0;
                                                    if (bmi270.ReadRaw(0x1B, &st5, 1) && (st5 & 0xC0)) { pd_drdy = 1; break; }
                                                    vTaskDelay(pdMS_TO_TICKS(80));
                                                }
                                                ESP_LOGI(TAG, "IMU diag PWRRESET_DRDY=%d", pd_drdy);
                                                if (pd_drdy) { bmi270_present = true; ok = true; }
                                            }
                                        }
                                    }
                                    if (drdy) {
                                        bmi270_present = true;
                                        ok = true;
                                    }
                                }
                            }
                            s_next_diag_ms = now_stat_ms + 30000;
                        }
                    }
                }
                continue;
            }
                static int64_t imu_select_ms = 0;
                if (!imu_selected_logged) {
                if (bmi270_present) {
                    uint8_t chip3 = 0; bmi270.ReadRaw(0x00, &chip3, 1);
                    const char* cname3 = (chip3 == 0x24 ? "BMI270" : (chip3 == 0x26 ? "BMI26x" : "BMI2"));
                    ESP_LOGI(TAG, "IMU device: %s addr=0x68 id=0x%02X", cname3, chip3);
                } else {
                    if (bma_present) {
                        ESP_LOGI(TAG, "IMU device: BMA423 addr=0x18");
                    } else {
                        ESP_LOGI(TAG, "IMU device: MPU6050 addr=0x68");
                    }
                }
                imu_selected_logged = true;
                imu_select_ms = esp_timer_get_time()/1000;
                }
            float ax, ay, az;
            bool read_ok = false;
            static int bmi_fail_count = 0;
            static int64_t s_imu_data_log_ms = 0;
            static int64_t s_next_raw_read_ms = 0;
            
            // Always enter block to allow fallback logic
            if (true) {
                if (bmi270_present) {
                    int64_t now_ms_gate = esp_timer_get_time()/1000;
                    if (s_i2c_quiet_until_ms > now_ms_gate) {
                        read_ok = false;
                    } else {
                        if (s_next_raw_read_ms == 0 || now_ms_gate >= s_next_raw_read_ms) {
                            read_ok = bmi270.ReadAccel(ax, ay, az);
                            s_next_raw_read_ms = now_ms_gate + 3000;
                            if (read_ok && !(ax == 0.0f && ay == 0.0f && az == 0.0f)) {
                                s_bmi_raw_valid = true;
                            }
                        } else {
                            read_ok = false;
                        }
                    }
                }
                int64_t now_stat_ms = esp_timer_get_time()/1000;
                if (now_stat_ms - s_imu_status_log_ms > 1000) {
                    int quiet_ms = (s_i2c_quiet_until_ms > now_stat_ms) ? (int)(s_i2c_quiet_until_ms - now_stat_ms) : 0;
                    ESP_LOGI(TAG, "IMU status: present(bmi=%d bma=%d) read_ok=%d quiet_remain=%dms zero_trials=%d raw_valid=%d", 
                        bmi270_present, bma_present, (int)read_ok, quiet_ms, bmi_zero_trials, (int)s_bmi_raw_valid);
                    s_imu_status_log_ms = now_stat_ms;
                }
                if (!read_ok) {
                    bmi_fail_count++;
                    if (bmi_fail_count >= 8) {
                        int64_t now_ms_cb = esp_timer_get_time()/1000;
                        s_i2c_quiet_until_ms = now_ms_cb + 60000;
                        ESP_LOGW(TAG, "IMU circuit-breaker engaged: quiet 60s due to repeated I2C timeouts");
                        bmi_fail_count = 0;
                    } else if (bmi_fail_count > 2) {
                        int64_t now_ms = esp_timer_get_time()/1000;
                        if ((now_ms - s_last_bmi_init_ms) > 5000) {
                            if (s_bmi_init_attempts < 2) {
                                if (bmi270.Initialize()) {
                                    s_bmi_raw_valid = true;
                                    ESP_LOGI(TAG, "BMI270 re-initialized successfully");
                                } else {
                                    ESP_LOGW(TAG, "BMI270 re-initialization failed");
                                    s_i2c_quiet_until_ms = now_ms + 5000; // backoff 5s to reduce I2C errors
                                }
                                s_last_bmi_init_ms = now_ms;
                                s_bmi_init_attempts++;
                            } else {
                                ESP_LOGW(TAG, "BMI270 persistent failure, scheduling hard reset/backoff and switching to BMA");
                                do_imu_hard_reset();
                                int backoffs[] = {10000, 30000, 60000, 120000};
                                int64_t now_ms2 = esp_timer_get_time()/1000;
                                s_next_bmi_try_ms = now_ms2 + backoffs[s_bmi_backoff_stage];
                                if (s_bmi_backoff_stage < 3) s_bmi_backoff_stage++;
                                if (!bma_present) {
                                    if (i2c_master_probe(self->i2c_bus_, 0x19, 100) == ESP_OK && bma19.Initialize()) {
                                        bma = &bma19; bma_addr = 0x19; bma_present = true; bma->EnableAccel(0xA8, 0x00);
                                    }
                                }
                                // Permanently pause BMI retries for 1 minute to reduce I2C errors
                                bmi270_present = false;
                                s_bmi_raw_valid = false;
                                s_next_bmi_try_ms = now_ms2 + 60000;
                            }
                        }
                        bmi_fail_count = 0;
                    }
                } else {
                    bmi_fail_count = 0;
                }
                if (!read_ok && bma_present && bma) {
                    float bax, bay, baz;
                    if (bma->ReadAccel(bax, bay, baz)) {
                        ax = bax; ay = bay; az = baz;
                        read_ok = true;
                        int64_t nqw = esp_timer_get_time()/1000;
                        s_coherence_quiet_until_ms = nqw + 1500;
                    }
                } else if (!read_ok && !bma_present) {
                    if (i2c_master_probe(self->i2c_bus_, 0x19, 100) == ESP_OK && bma19.Initialize()) {
                        bma = &bma19; bma_addr = 0x19; bma_present = true; bma->EnableAccel(0xA8, 0x00);
                        float bax, bay, baz;
                        if (bma->ReadAccel(bax, bay, baz)) {
                            ax = bax; ay = bay; az = baz;
                            read_ok = true;
                            int64_t nqw2 = esp_timer_get_time()/1000;
                            s_coherence_quiet_until_ms = nqw2 + 1500;
                        }
                    }
                }
                if (!read_ok) {
                    float mx, my, mz;
                    if (mpu.ReadAccel(mx, my, mz)) {
                        ax = mx; ay = my; az = mz;
                        read_ok = true;
                        int64_t nqw3 = esp_timer_get_time()/1000;
                        s_coherence_quiet_until_ms = nqw3 + 1500;
                    }
                }
                if (!read_ok || (ax == 0.0f && ay == 0.0f && az == 0.0f)) {
                    bmi_zero_trials++;
                    if (bmi_zero_trials >= 2) {
                        bmi270.EnsureEnabled();
                    }
                } else {
                    bmi_zero_trials = 0;
                }
                static const bool kAllowBmaSupplement = true;
                static int64_t s_next_bma_supp_ms = 0;
                    if (kAllowBmaSupplement && bmi_zero_trials >= 3) {
                        int64_t now_ms_bma = esp_timer_get_time()/1000;
                        if (s_next_bma_supp_ms != 0 && now_ms_bma < s_next_bma_supp_ms) {
                            // rate limit
                        } else {
                            s_next_bma_supp_ms = now_ms_bma + 10000;
                        if (!bma_present) {
                            if (i2c_master_probe(self->i2c_bus_, 0x19, 100) == ESP_OK && bma19.Initialize()) {
                                bma = &bma19; bma_addr = 0x19;
                                bma_present = true;
                                bma->EnableAccel(0xA8, 0x00);
                            }
                        }
                        if (bma_present) {
                            bool got = false;
                            float bax = ax, bay = ay, baz = az;
                            if (bma && bma->ReadAccel(bax, bay, baz)) {
                                got = true;
                            }
                            if (!got) {
                                if (bma) {
                                    if (bma->ReadAccel(bax, bay, baz)) {
                                        got = true;
                                    }
                                }
                            }
                        if (got) {
                            ax = bax; ay = bay; az = baz;
                            read_ok = true;
                            int64_t now = esp_timer_get_time()/1000;
                            s_coherence_quiet_until_ms = now + 1500;
                            if (now - last_imu_log_ms > 1000) {
                                if (bma) { uint8_t rb[6]; bma->ReadRaw6(rb);
                                ESP_LOGI(TAG, "BMA ACC sup=%02X%02X%02X%02X%02X%02X ax=%.2f ay=%.2f az=%.2f",
                                    rb[0], rb[1], rb[2], rb[3], rb[4], rb[5], ax, ay, az); }
                            }
                        }
                    }
                    }
                }
                if (read_ok) {
                    float mag_est = fabsf(ax) + fabsf(ay) + fabsf(az);
                    if (kAllowBmaSupplement && mag_est < 0.10f) {
                        if (!bma_present && i2c_master_probe(self->i2c_bus_, 0x19, 100) == ESP_OK) {
                            if (bma19.Initialize()) {
                                bma = &bma19;
                                bma_present = true;
                                bma->EnableAccel(0xA8, 0x00);
                            }
                        }
                        if (bma_present && bma) {
                            float bax, bay, baz;
                            if (bma->ReadAccel(bax, bay, baz)) {
                                ax = bax; ay = bay; az = baz;
                            }
                        }
                    }
                    int64_t now_log_ms = esp_timer_get_time()/1000;
                    if (now_log_ms - s_imu_data_log_ms > 2000) {
                        ESP_LOGI(TAG, "IMU ax=%.2f ay=%.2f az=%.2f mag=%.2f", ax, ay, az, mag_est);
                        s_imu_data_log_ms = now_log_ms;
                    }
                }
                if (bmi_zero_trials >= 6 || bmi_fail_count > 3) {
                    int64_t now_ms = esp_timer_get_time()/1000;
                    if ((now_ms - s_last_bmi_init_ms) > 8000 && s_bmi_init_attempts < 6) {
                        bmi270.Initialize();
                        bmi270.EnsureEnabled();
                        s_filter_init = false;
                        s_last_bmi_init_ms = now_ms;
                        s_bmi_init_attempts++;
                    }
                    bmi_fail_count = 0;
                }
            }
            if (!bmi270_present) {
                if (bma_present && bma) {
                    read_ok = bma->ReadAccel(ax, ay, az);
                    if (read_ok) {
                        // Check for all-zeros which might indicate a stuck sensor or unpowered
                        static int s_bma_zero_cnt = 0;
                        if (fabsf(ax) < 0.001f && fabsf(ay) < 0.001f && fabsf(az) < 0.001f) {
                            s_bma_zero_cnt++;
                            if (s_bma_zero_cnt > 50) {
                                ESP_LOGW(TAG, "BMA423 all zeros for >1s, re-initializing...");
                                bma->Initialize();
                                bma->EnableAccel(0xA8, 0x00);
                                s_bma_zero_cnt = 0;
                            }
                            if ((s_bma_zero_cnt % 50) == 0) {
                                ESP_LOGW(TAG, "BMA423 data all zeros? cnt=%d", s_bma_zero_cnt);
                            }
                            read_ok = false; 
                        } else {
                            s_bma_zero_cnt = 0;
                        }
                    } else {
                         static int s_bma_fail_cnt = 0;
                         if ((++s_bma_fail_cnt % 50) == 0) {
                             ESP_LOGW(TAG, "BMA423 ReadAccel failed");
                         }
                    }
                } else {
                    if (!s_mpu_inited) {
                        s_mpu_inited = mpu.Initialize();
                        if (s_mpu_inited) {
                            ESP_LOGI(TAG, "IMU device: MPU6050 addr=0x68");
                        }
                    }
                    read_ok = mpu.ReadAccel(ax, ay, az);
                }
            }
            if (read_ok) {
                s_fail_log_cnt = 0;
                int64_t now = esp_timer_get_time()/1000;
                
                float cur_mag = fabsf(ax) + fabsf(ay) + fabsf(az);

                // Reset filters on startup or when recovering from zeros to avoid false shake trigger
                if (!s_filter_init || (cur_mag > 0.5f && (fabsf(avg_ax) + fabsf(avg_ay) + fabsf(avg_az)) < 0.1f)) {
                    avg_ax = ax; avg_ay = ay; avg_az = az;
                    prev_ax = ax; prev_ay = ay; prev_az = az;
                    s_filter_init = true;
                }

                float jerk = fabsf(ax - prev_ax) + fabsf(ay - prev_ay) + fabsf(az - prev_az);
                float mag = fabsf(ax) + fabsf(ay) + fabsf(az);
                float mag_diff = fabsf(mag - prev_mag);
                
                bool fallback_mode = !s_bmi_raw_valid;
                float alpha = (fallback_mode ? 0.95f : 0.99f);
                float one_minus_alpha = 1.0f - alpha;
                avg_ax = alpha * avg_ax + one_minus_alpha * ax;
                avg_ay = alpha * avg_ay + one_minus_alpha * ay;
                avg_az = alpha * avg_az + one_minus_alpha * az;
                float dyn = fabsf((ax - avg_ax)) + fabsf((ay - avg_ay)) + fabsf((az - avg_az));
                if (jerk < 0.001f && mag_diff > 0.06f) {
                    float capped = (mag_diff > 0.08f) ? 0.08f : mag_diff;
                    jerk = capped;
                }
                
                // Update previous values for next iteration's jerk calculation
                prev_ax = ax; prev_ay = ay; prev_az = az;

                static int s_bma_stuck_count = 0;
                static int s_bma_conf_index = 0;
                static int s_bma_range_index = 0;
                static int s_bma_recover_stage = 0;
                static const uint8_t kBmaConfs[3] = {0xA8, 0xA4, 0xAC};
                static const uint8_t kBmaRanges[4] = {0x00, 0x01, 0x02, 0x03};
                if (bma_present) {
                    bool saturated = (fabsf(ax) > 3.5f && fabsf(ay) > 3.5f && fabsf(az) > 3.5f);
                    // bool no_dyn = (dyn < 0.002f && jerk < 0.004f);
                    if (saturated) {
                        s_bma_stuck_count++;
                    } else if (s_bma_stuck_count > 0) {
                        s_bma_stuck_count--;
                    }
                    if (s_bma_stuck_count >= 25) {
                        uint32_t hz = (s_bma_recover_stage == 0 ? 100000u : (s_bma_recover_stage == 1 ? 50000u : 25000u));
                        uint16_t w = (s_bma_recover_stage == 0 ? 5u : (s_bma_recover_stage == 1 ? 8u : 12u));
                        if (bma) { bma->Reattach(hz, w); }
                        if (bma) bma->Initialize();
                        uint8_t conf = kBmaConfs[s_bma_conf_index];
                        uint8_t rng = kBmaRanges[s_bma_range_index];
                        if (bma) bma->EnableAccel(conf, rng);
                        s_bma_conf_index = (s_bma_conf_index + 1) % 3;
                        s_bma_range_index = (s_bma_range_index + (s_bma_conf_index == 0 ? 1 : 0)) % 4;
                        ESP_LOGI(TAG, "BMA reconfig applied: conf=%02X range=%02X hz=%u wait=%u stage=%d", conf, rng, (unsigned)hz, (unsigned)w, s_bma_recover_stage);
                        s_bma_stuck_count = 0;
                        if (s_bma_recover_stage < 2) s_bma_recover_stage++; else s_bma_recover_stage = 0;
                        vTaskDelay(pdMS_TO_TICKS(100));
                    }
                    
                    // Check for frozen data (identical values)
                    static float s_last_ax = 0, s_last_ay = 0, s_last_az = 0;
                    static int s_frozen_cnt = 0;
                    if (ax == s_last_ax && ay == s_last_ay && az == s_last_az) {
                        s_frozen_cnt++;
                        if (s_frozen_cnt % 20 == 0) {
                             ESP_LOGW(TAG, "BMA423 potential frozen data cnt=%d ax=%.3f", s_frozen_cnt, ax);
                        }
                        if (s_frozen_cnt > 50) { // 1 second
                             ESP_LOGW(TAG, "BMA423 frozen data detected, forcing re-init...");
                             if (bma) {
                                 bma->Reattach(100000, 0);
                                 bma->Initialize();
                                 bma->EnableAccel(0xA8, 0x00);
                             }
                             s_frozen_cnt = 0;
                        }
                    } else {
                        s_frozen_cnt = 0;
                    }
                    s_last_ax = ax; s_last_ay = ay; s_last_az = az;
                }
                if (!bma_present && !bmi270_present) {
                    static float s_mpu_last_ax = 0, s_mpu_last_ay = 0, s_mpu_last_az = 0;
                    static int s_mpu_frozen_cnt = 0;
                    if (ax == s_mpu_last_ax && ay == s_mpu_last_ay && az == s_mpu_last_az) {
                        s_mpu_frozen_cnt++;
                        if (s_mpu_frozen_cnt > 50) {
                            mpu.Initialize();
                            s_filter_init = false;
                            s_mpu_frozen_cnt = 0;
                        }
                    } else {
                        s_mpu_frozen_cnt = 0;
                    }
                    s_mpu_last_ax = ax; s_mpu_last_ay = ay; s_mpu_last_az = az;
                }
                if (now - last_imu_log_ms > 1000) {
                    ESP_LOGI(TAG, "IMU jerk=%.3f dyn=%.3f ax=%.2f ay=%.2f az=%.2f score=%d", jerk, dyn, ax, ay, az, shake_score);
                    if (bmi270_present) {
                        static int64_t s_bmi_status_quiet_until_ms = 0;
                        int64_t nowms = esp_timer_get_time()/1000;
                        if (nowms >= s_bmi_status_quiet_until_ms) {
                            uint8_t acc[6]{};
                        uint8_t gyr[6]{};
                        uint8_t time0 = 0;
                        uint8_t buf[13]{};
                        bool ok_block = bmi270.ReadRaw(0x0C, buf, 13);
                        if (ok_block) {
                            memcpy(acc, &buf[0], 6); // 0x0C-0x11 is Accel
                            memcpy(gyr, &buf[6], 6); // 0x12-0x17 is Gyro
                            time0 = buf[12];
                        }
                        bool ok_acc = ok_block;
                        bool ok_gyr = ok_block;
                        bool ok_tm = ok_block;
                        if (ok_acc && ok_gyr) {
                            uint8_t pwr = 0, err = 0, st = 0, int_st = 0, chip_id = 0, acc_conf = 0;
                            bool ok_pwr = bmi270.ReadRaw(0x7D, &pwr, 1);
                            bool ok_err = bmi270.ReadRaw(0x02, &err, 1);
                            bool ok_st  = bmi270.ReadRaw(0x1B, &st, 1);
                            bool ok_int = bmi270.ReadRaw(0x21, &int_st, 1);
                            bool ok_id  = bmi270.ReadRaw(0x00, &chip_id, 1);
                            bool ok_cf  = bmi270.ReadRaw(0x40, &acc_conf, 1);
                            bool valid_data = ((acc[0]|acc[1]|acc[2]|acc[3]|acc[4]|acc[5]|gyr[0]|gyr[1]|gyr[2]|gyr[3]|gyr[4]|gyr[5]) != 0);
                            s_bmi_raw_valid = valid_data; // Only update validity on successful read
                            
                            if (valid_data) {
                                s_fail_log_cnt = 0;
                            }

                            ESP_LOGI(TAG, "BMI ACC=%02X%02X%02X%02X%02X%02X GYR=%02X%02X%02X%02X%02X%02X Pwr=%02X Time=%u ERR=%02X STAT=%02X INT_ST=%02X ID=%02X CONF=%02X VALID=%d",
                                acc[0], acc[1], acc[2], acc[3], acc[4], acc[5],
                                gyr[0], gyr[1], gyr[2], gyr[3], gyr[4], gyr[5],
                                ok_pwr ? pwr : 0, ok_tm ? time0 : 0, ok_err ? err : 0, ok_st ? st : 0,
                                ok_int ? int_st : 0, ok_id ? chip_id : 0, ok_cf ? acc_conf : 0, s_bmi_raw_valid);
                                static uint8_t s_bmi_last_time0 = 0;
                                static int s_bmi_time_stuck_sec = 0;
                                if (ok_tm) {
                                    if (time0 == s_bmi_last_time0) {
                                        s_bmi_time_stuck_sec++;
                                    } else {
                                        s_bmi_time_stuck_sec = 0;
                                    }
                                    s_bmi_last_time0 = time0;
                                }
                        if (s_bmi_time_stuck_sec >= 4) {
                            static int s_bmi_recover_stage = 0;
                            static int s_bmi_recover_attempts = 0;
                            uint32_t hz = (s_bmi_recover_stage == 0 ? 100000u : (s_bmi_recover_stage == 1 ? 50000u : 25000u));
                            uint16_t w = (s_bmi_recover_stage == 0 ? 5u : (s_bmi_recover_stage == 1 ? 8u : 12u));
                            // gpio_set_level(POWER_CTRL, 1);
                            // vTaskDelay(pdMS_TO_TICKS(50));
                            // gpio_set_level(POWER_CTRL, 0);
                            // vTaskDelay(pdMS_TO_TICKS(50));
                            bmi270.Reattach(hz, w);
                            bmi270.Initialize();
                            bmi270.EnsureEnabled();
                            s_filter_init = false;
                            s_bmi_time_stuck_sec = 0;
                            s_bmi_recover_stage = (s_bmi_recover_stage < 2) ? (s_bmi_recover_stage + 1) : 0;
                            s_bmi_recover_attempts++;
                            if (s_bmi_recover_attempts >= 3 && !s_bmi_raw_valid) {
                                ESP_LOGW(TAG, "BMI disabled due to persistent I2C failures; falling back to BMA/MPU");
                                bmi270_present = false;
                                // Force enable BMA as primary
                                if (!bma_present) {
                                    if (i2c_master_probe(self->i2c_bus_, 0x19, 100) == ESP_OK) {
                                        bma = &bma19; bma_addr = 0x19; bma_present = true;
                                    }
                                }
                                if (bma_present && bma) {
                                    ESP_LOGI(TAG, "Forcing BMA initialization for fallback...");
                                    bma->Initialize();
                                    bma->EnableAccel(0xAC, 0x00);
                                }
                            }
                        }
                            } else {
                                s_bmi_raw_valid = false;
                                s_bmi_status_quiet_until_ms = nowms + 5000;
                            }
                        }
                        static int64_t s_diag_tick_ms = 0;
                        int64_t nowdt = esp_timer_get_time()/1000;
                        if (s_diag_tick_ms == 0 || (nowdt - s_diag_tick_ms) >= 5000) {
                            uint8_t buf2[13]{};
                            bool ok2 = bmi270.ReadRaw(0x0C, buf2, 13);
                            int acc_nz = (buf2[0]|buf2[1]|buf2[2]|buf2[3]|buf2[4]|buf2[5]) != 0;
                            int gyr_nz = (buf2[6]|buf2[7]|buf2[8]|buf2[9]|buf2[10]|buf2[11]) != 0;
                            uint8_t st2 = 0;
                            (void)bmi270.ReadRaw(0x1B, &st2, 1);
                            ESP_LOGI(TAG, "IMU diag tick: ok=%d DRDY=%d ACC_NZ=%d GYR_NZ=%d", (int)ok2, (int)((st2 & 0xC0) ? 1 : 0), acc_nz, gyr_nz);
                            s_diag_tick_ms = nowdt;
                        }
                    }
                    if (bma_present) {
                        static i2c_master_dev_handle_t s_bma_mon = nullptr;
                        if (!s_bma_mon) {
                            i2c_device_config_t cfgm = {
                                .dev_addr_length = I2C_ADDR_BIT_LEN_7,
                                .device_address = (uint16_t)(bma_addr ? bma_addr : 0x19),
                                .scl_speed_hz = 100 * 1000,
                                .scl_wait_us = 5,
                                .flags = {.disable_ack_check = 0},
                            };
                            i2c_master_bus_add_device(self->i2c_bus_, &cfgm, &s_bma_mon);
                        }
                        
                    }
                    last_imu_log_ms = now;
                }
                static float s_energy = 0.0f;
                static int s_count = 0;
                static int s_static_count = 0;
                float diff = jerk + dyn;
                s_energy = s_energy * 0.7f + diff;
                bool shake_detected = false;
                bool in_quiet = (s_coherence_quiet_until_ms != 0 && now < s_coherence_quiet_until_ms);
                bool saturated_now = (fabsf(ax) > 3.5f && fabsf(ay) > 3.5f && fabsf(az) > 3.5f);
                float dyn_th = fallback_mode ? 0.05f : 0.08f;
                float jerk_th = fallback_mode ? 0.06f : 0.05f;
                bool candidate = ((dyn > dyn_th) || (jerk > jerk_th)) && !saturated_now && (mag > 0.12f) && !in_quiet;
                bool emote_ready = false;
                if (auto emo = dynamic_cast<emote::EmoteDisplay*>(display)) {
                    auto d1 = emo->GetEmojiData(std::string("silly"));
                    auto d2 = emo->GetEmojiData(std::string("neutral"));
                    emote_ready = (d1.data != nullptr) && (d2.data != nullptr);
                }
                static int s_flip_count = 0;
                static int64_t s_last_flip_ms = 0;
                float axd = ax - avg_ax;
                float flip_th = fallback_mode ? 0.03f : 0.06f;
                int sign = (axd > flip_th) ? 1 : ((axd < -flip_th) ? -1 : 0);
                if (sign != 0) {
                    int64_t now_ms = esp_timer_get_time()/1000;
                    if (s_last_flip_ms == 0) s_last_flip_ms = now_ms;
                    static int s_last_sign = 0;
                    if (s_last_sign != 0 && sign != s_last_sign && (now_ms - s_last_flip_ms) < 1200) {
                        s_flip_count++;
                    }
                    s_last_sign = sign;
                    s_last_flip_ms = now_ms;
                } else {
                    if (s_flip_count > 0) s_flip_count--;
                }
                if (candidate && mag > 0.10f && !in_quiet) {
                    s_count++;
                } else if (s_count > 0) {
                    s_count--;
                }
                if (emote_ready && ((s_count >= 4 && s_energy > 0.08f) || (s_flip_count >= 3 && s_energy > 0.08f)) && mag > 0.15f) {
                    shake_detected = true;
                    s_count = 0;
                    s_flip_count = 0;
                    s_energy = 0;
                    s_static_count = 0;
                }
                static int s_fast_trigger_cnt = 0;
                static int64_t s_fast_window_start_ms = 0;
                float axis_delta = fabsf(ax - prev_ax) + fabsf(ay - prev_ay) + fabsf(az - prev_az);
                bool fast_candidate = fallback_mode && mag > 0.12f && (
                    ((jerk > 0.08f || dyn > 0.035f) && axis_delta > 0.08f) ||
                    (mag_diff > 0.10f && axis_delta > 0.08f)
                ) && !in_quiet;
                int64_t now_ms_fc = esp_timer_get_time()/1000;
                if (fast_candidate) {
                    if (s_fast_window_start_ms == 0 || (now_ms_fc - s_fast_window_start_ms) > 1000) {
                        s_fast_window_start_ms = now_ms_fc;
                        s_fast_trigger_cnt = 0;
                    }
                    s_fast_trigger_cnt++;
                } else {
                    if (s_fast_window_start_ms != 0 && (now_ms_fc - s_fast_window_start_ms) > 1000) {
                        s_fast_window_start_ms = 0;
                        s_fast_trigger_cnt = 0;
                    }
                }
                if (!shake_detected && s_fast_trigger_cnt >= 3 && fallback_mode && mag > 0.12f) {
                    shake_detected = true;
                    s_fast_trigger_cnt = 0;
                    s_fast_window_start_ms = 0;
                    s_count = 0;
                    s_flip_count = 0;
                    s_energy = 0;
                    s_static_count = 0;
                }
                // Do not suppress shake purely due to zero vectors when fallback is active; rely on any-motion and fast window
                if (bmi270_present && ax == 0.0f && ay == 0.0f && az == 0.0f && !fallback_mode) {
                    shake_detected = false;
                }
                if (bmi270_present && !shake_detected && s_bmi_raw_valid) {
                    if (emote_ready && mag > 0.12f && bmi270.UpdateShakeDetection()) {
                        shake_detected = true;
                        ESP_LOGI(TAG, "IMU shake: energy path trigger");
                        s_count = 0;
                        s_energy = 0;
                        s_static_count = 0;
                    }
                }
                if (bmi270_present && !shake_detected) {
                    static int64_t s_next_anymotion_check_ms = 0;
                    float axis_delta = fabsf(ax - prev_ax) + fabsf(ay - prev_ay) + fabsf(az - prev_az);
                    int64_t now_ms_any = esp_timer_get_time()/1000;
                    bool can_check_any = (s_next_anymotion_check_ms == 0 || now_ms_any >= s_next_anymotion_check_ms) && (s_i2c_quiet_until_ms <= now_ms_any);
                    if (can_check_any && emote_ready && s_bmi_raw_valid && (bmi270.CheckAnyMotion() || bmi270.CheckAnyMotionRaw()) && (mag > 0.12f || axis_delta > 0.08f || dyn > 0.035f)) {
                        shake_detected = true;
                        ESP_LOGI(TAG, "IMU shake: any-motion trigger");
                        s_count = 0;
                        s_energy = 0;
                        s_static_count = 0;
                        s_next_anymotion_check_ms = now_ms_any + 3000;
                    } else {
                        if (!can_check_any) {
                            // skip check due to quiet or gating
                        } else {
                            s_next_anymotion_check_ms = now_ms_any + 1500;
                        }
                    }
                }
                if ((now - imu_select_ms) < 2000) {
                    shake_detected = false;
                }
                if ((now - s_boot_ms) < 6000) {
                    shake_detected = false;
                }

                if (shake_detected && (now - last_dizzy_ms) > 1200) {
                    bool shown = false;
                    if (auto emo = dynamic_cast<emote::EmoteDisplay*>(display)) {
                        auto assets = emo->GetEmojiData(std::string("silly"));
                        if (assets.data) {
                            display->SetEmotion("silly");
                            shown = true;
                        } else {
                            auto nassets = emo->GetEmojiData(std::string("neutral"));
                            if (nassets.data) {
                                display->SetEmotion("neutral");
                                shown = true;
                            }
                        }
                    }
                    if (!shown) {
                        shown = true;
                    }
                    ESP_LOGI(TAG, "IMU silly triggered (shake)");
                    last_dizzy_ms = now;
                    last_emote_time = (int)now;
                    last_emote_code = 7;
                }
                if (stuck_count >= 10) {
                    if (bmi270_present) {
                        bmi270.Initialize();
                        s_filter_init = false;
                    } else {
                        ok = mpu.Initialize();
                    }
                    stuck_count = 0;
                    imu_selected_logged = false;
                }
                static int s_no_dyn_cnt = 0;
                if (bma_present) {
                    if (dyn < 0.005f && jerk < 0.005f) {
                        s_no_dyn_cnt++;
                        if (s_no_dyn_cnt >= 50) {
                            if (bma) {
                                bma->Reattach(100000, 5);
                                bma->Initialize();
                                bma->EnableAccel(0xA8, 0x00);
                            }
                            s_filter_init = false;
                            s_no_dyn_cnt = 0;
                        }
                    } else {
                        s_no_dyn_cnt = 0;
                    }
                }
                if (!bmi270_present && bma_present && s_static_count > 20) {
                    if (bma) {
                        bma->Initialize();
                        bma->EnableAccel(0xA8, 0x00);
                    }
                    s_static_count = 0;
                }
                prev_ax = ax; prev_ay = ay; prev_az = az; prev_mag = mag;
            }

            if (!read_ok) {
                s_filter_init = false;
                if ((++s_fail_log_cnt % 200) == 0) {
                    ESP_LOGW(TAG, "IMU read failed (cnt=%d) bmi=%d bma=%d", s_fail_log_cnt, bmi270_present, bma_present);
                    // If BMI270 fails consistently, disable it to allow fallback to BMA
                    if (bmi270_present && s_fail_log_cnt >= 10) {
                        ESP_LOGE(TAG, "BMI270 unstable, scheduling hard reset and BMA fallback");
                        do_imu_hard_reset();
                        int backoffs[] = {10000, 30000, 60000, 120000};
                        int64_t now_ms3 = esp_timer_get_time()/1000;
                        s_next_bmi_try_ms = now_ms3 + backoffs[s_bmi_backoff_stage];
                        if (s_bmi_backoff_stage < 3) s_bmi_backoff_stage++;
                        if (!bma_present) {
                            vTaskDelay(pdMS_TO_TICKS(20));
                            if (i2c_master_probe(self->i2c_bus_, 0x19, 100) == ESP_OK && bma19.Initialize()) {
                                bma = &bma19; bma_addr = 0x19; bma_present = true; bma->EnableAccel(0xA8, 0x00);
                                ESP_LOGI(TAG, "BMA423 enabled at 0x19 (fallback)");
                            } else {
                                if (!s_mpu_inited && mpu.Initialize()) {
                                    s_mpu_inited = true;
                                    ESP_LOGW(TAG, "Fallback to MPU6050");
                                }
                            }
                        }
                        s_fail_log_cnt = 0;
                    }
                    // If BMA is active but failing (e.g. all zeros), try to re-init
                    if (!bmi270_present && bma_present && s_fail_log_cnt >= 20) {
                        ESP_LOGE(TAG, "BMA423 unstable/zeros, attempting re-init");
                        if (bma) {
                            bma->Reattach(100000, 0);
                            bma->Initialize();
                            bma->EnableAccel(0xA8, 0x00);
                        }
                        s_fail_log_cnt = 0;
                    }
                }
            }
            // Escalating backoff for BMI retries to reduce I2C timeouts
            int64_t now_loop_ms = esp_timer_get_time()/1000;
            if (!bmi270_present && !bma_present) {
                if (s_next_bmi_try_ms == 0 || now_loop_ms >= s_next_bmi_try_ms) {
                    if (s_i2c_quiet_until_ms > now_loop_ms) {
                        // still in quiet window, skip
                    } else {
                        // Hard reset then try ultra low-speed reattach and initialize on 0x68/0x69
                        do_imu_hard_reset();
                        vTaskDelay(pdMS_TO_TICKS(20));
                        bmi270.Reattach(25000, 20);
                        bmi270.SetAddress(0x68);
                        bool ok68 = bmi270.Initialize();
                        bool recovered = false;
                        if (ok68) {
                            recovered = true;
                        } else {
                            bmi270.SetAddress(0x69);
                            if (bmi270.Initialize()) recovered = true;
                        }
                        if (recovered) {
                            uint8_t who = 0; bmi270.ReadRaw(0x00, &who, 1);
                            ESP_LOGI(TAG, "BMI WHOAMI=0x%02X", who);
                            s_bmi_backoff_stage = 0;
                            s_bmi_init_attempts = 0;
                            s_bmi_raw_valid = true;
                            bmi270_present = true;
                            s_next_bmi_try_ms = 0;
                            ESP_LOGI(TAG, "BMI270/BMI26x recovered after backoff");
                        } else {
                            int backoffs2[] = {30000, 60000, 120000, 180000};
                            s_next_bmi_try_ms = now_loop_ms + backoffs2[s_bmi_backoff_stage];
                            if (s_bmi_backoff_stage < 3) s_bmi_backoff_stage++;
                            s_i2c_quiet_until_ms = now_loop_ms + 5000;
                        }
                    }
                }
            }
            vTaskDelay(pdMS_TO_TICKS((bmi270_present || bma_present) ? 80 : 350));
        }
    }

public:
    std::string GetDeviceStatusJson() override {
        cJSON* root=cJSON_Parse(WifiBoard::GetDeviceStatusJson().c_str());
        if (!root) root=cJSON_CreateObject();
        auto motion=cJSON_AddObjectToObject(root,"motion");
        const int64_t now=esp_timer_get_time()/1000;
        cJSON_AddNumberToObject(motion,"actual",g_base_actual.load());
        cJSON_AddBoolToObject(motion,"moving",g_base_moving.load());
        cJSON_AddNumberToObject(motion,"fault",g_base_fault.load());
        cJSON_AddNumberToObject(motion,"feedback_age_ms",now-g_base_feedback_ms.load());
        cJSON_AddNumberToObject(motion,"raw_angle",g_doa_raw.load());
        cJSON_AddNumberToObject(motion,"speech_delta",g_doa_last_angle.load());
        cJSON_AddNumberToObject(motion,"speech_age_ms",now-g_doa_last_ms.load());
        cJSON_AddNumberToObject(motion,"front_angle",g_front_angle.load());
        cJSON_AddBoolToObject(motion,"tracking",g_tracking_enabled.load());
        char* json=cJSON_PrintUnformatted(root);
        std::string out(json);cJSON_free(json);cJSON_Delete(root);
        return out;
    }
    EspS3Cat() : boot_button_(BOOT_BUTTON_GPIO)
    {
        {
            Settings settings("tracking",false);
            g_front_angle.store(std::clamp<int>(settings.GetInt("front_angle",90),60,120));
        }
        McpServer::GetInstance().AddUserOnlyTool("self.motion.configure_tracking",
            "Configure sound tracking; front_angle is the measured broadside DOA zero, not a motor target.",
            PropertyList({Property("enabled",kPropertyTypeBoolean),Property("front_angle",kPropertyTypeInteger,60,120)}),
            [](const PropertyList& p)->ReturnValue {
                const int front=p["front_angle"].value<int>();
                Settings settings("tracking",true);
                settings.SetInt("front_angle",front);
                g_front_angle.store(front);
                g_tracking_enabled.store(p["enabled"].value<bool>());
                return true;
            });
        InitializeI2c();
        uint8_t pcb_verison = DetectPcbVersion();
        if (!imu_diag_isolation_) { InitializeCharge(); }
        if (!imu_diag_isolation_) { InitializeCst816sTouchPad(); }

        InitializeSpi();
        Initializest77916Display(pcb_verison);
        InitializeButtons();
        
        esp_timer_create_args_t doa_timer_args = {
            .callback = [](void* arg){
                auto& board = (EspS3Cat&)Board::GetInstance();
                auto* codec = board.GetAudioCodec();
                ESP_LOGI(TAG, "Starting DOA task");
                xTaskCreatePinnedToCore(SoundSourceLocator::TaskEntry, "doa_base", 16 * 1024, new SoundSourceLocator(codec), 4, NULL, 1);
            },
            .arg = this,
            .dispatch_method = ESP_TIMER_TASK,
            .name = "doa_start",
            .skip_unhandled_events = true,
        };
        esp_timer_create(&doa_timer_args, &doa_start_timer_);
        if (doa_enabled_) {
            esp_timer_start_once(doa_start_timer_, 5000000);
        }

        
        if (BOARD_ENABLE_IMU) {
            xTaskCreatePinnedToCore(imu_task, "imu_task", 4 * 1024, this, 4, NULL, 0);
        } else {
            ESP_LOGW(TAG, "IMU disabled by config (BOARD_ENABLE_IMU=0), skip imu_task");
        }
    }

    virtual AudioCodec* GetAudioCodec() override
    {
        static EchoEarAudioCodec audio_codec(
            i2c_bus_,
            AUDIO_INPUT_SAMPLE_RATE,
            AUDIO_OUTPUT_SAMPLE_RATE,
            AUDIO_I2S_GPIO_MCLK,
            AUDIO_I2S_GPIO_BCLK,
            AUDIO_I2S_GPIO_WS,
            AUDIO_I2S_GPIO_DOUT,
            AUDIO_I2S_GPIO_DIN,
            AUDIO_CODEC_PA_PIN,
            AUDIO_CODEC_ES8311_ADDR,
            AUDIO_CODEC_ES7210_ADDR);
        return &audio_codec;
    }

    virtual Display* GetDisplay() override
    {
        return display_;
    }

    Cst816s* GetTouchpad()
    {
        return cst816s_;
    }

    virtual Backlight* GetBacklight() override
    {
        return backlight_;
    }
};

DECLARE_BOARD(EspS3Cat);

