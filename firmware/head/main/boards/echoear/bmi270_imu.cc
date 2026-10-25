#include "bmi270_imu.h"
#include <esp_log.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <string.h>
#include <stdlib.h>
#include <math.h>
#include "config.h"
#include <driver/gpio.h>

#include "bmi270_context.h"
#include "bmi2.h"
#include <esp_rom_sys.h>

static const char* TAG_BMI = "BMI270";


static inline uint16_t bmi_u16_from_le(uint8_t lsb, uint8_t msb) { return (uint16_t)lsb | ((uint16_t)msb << 8); }
static int64_t s_i2c_driver_quiet_until_ms = 0;

bool BMI270Imu::Initialize() {
    // Always reattach with standard speed for initialization reliability
    Reattach(100000, 0);
    
    dev_.intf = BMI2_I2C_INTF;
    dev_.intf_ptr = this;
    dev_.read = &BMI270Imu::BmiRead;
    dev_.write = &BMI270Imu::BmiWrite;
    dev_.delay_us = &BMI270Imu::BmiDelayUs;
    dev_.read_write_len = 32; // Optimized buffer length
    // WDT settings removed to use default behavior

    auto sread = [&](uint8_t reg, uint8_t* data, uint32_t len) -> bool {
        for(int k=0; k<3; k++) {
            if (i2c_master_transmit_receive(i2c_device_, &reg, 1, data, len, 50) == ESP_OK) return true;
            esp_rom_delay_us(100);
        }
        return false;
    };

    chip_id_ = 0;
    for (int i = 0; i < 3; i++) {
        uint8_t cid = 0;
        if (sread(0x00, &cid, 1) && cid != 0) {
            chip_id_ = cid;
            break;
        }
        BmiDelayUs(10000, nullptr);
    }

    if (chip_id_ == 0) {
        ESP_LOGE(TAG_BMI, "Failed to read Chip ID, device unresponsive");
        return false;
    }
    ESP_LOGD(TAG_BMI, "Chip ID detected: 0x%02X", chip_id_);

    dev_.chip_id = chip_id_; // Set chip ID in dev structure for driver checks

    // Perform soft reset before any initialization attempt to clear stuck state
    uint8_t soft_rst_cmd = 0xB6;
    uint8_t rst_buf[2] = {0x7E, soft_rst_cmd};
    i2c_master_transmit(i2c_device_, rst_buf, 2, 50);
    BmiDelayUs(20000, nullptr); // Wait for reset
    
    int8_t rslt = BMI2_E_COM_FAIL;
    
    // Try standard initialization first
    if (chip_id_ == 0x24) {
        rslt = bmi270_init(&dev_);
    } else if (chip_id_ == 0x26) {
        ESP_LOGD(TAG_BMI, "Attempting context init for BMI26x (0x26)...");
        rslt = bmi270_context_init(&dev_);
        if (rslt != BMI2_OK) {
            ESP_LOGD(TAG_BMI, "Context init failed (rslt=%d), retrying with legacy...", rslt);
            BmiDelayUs(10000, nullptr);
            rslt = bmi270_legacy_init(&dev_);
        }
    } else {
        ESP_LOGE(TAG_BMI, "Unknown or missing chip ID: 0x%02X, skipping standard init", chip_id_);
        rslt = BMI2_E_COM_FAIL;
    }
    
    ESP_LOGD(TAG_BMI, "Standard init result: %d", rslt);

    if (rslt == BMI2_OK) {
            bmi2_sens_config sens_cfg[2];
            uint8_t types[2];
            types[0] = BMI2_ACCEL;
            types[1] = BMI2_GYRO;
            sens_cfg[0].type = BMI2_ACCEL;
            sens_cfg[0].cfg.acc.odr = BMI2_ACC_ODR_100HZ;
            sens_cfg[0].cfg.acc.bwp = 0x02;
            sens_cfg[0].cfg.acc.filter_perf = 1;
            sens_cfg[0].cfg.acc.range = BMI2_ACC_RANGE_2G;
            sens_cfg[1].type = BMI2_GYRO;
            sens_cfg[1].cfg.gyr.odr = BMI2_GYR_ODR_100HZ;
            sens_cfg[1].cfg.gyr.bwp = 0x02;
            sens_cfg[1].cfg.gyr.filter_perf = 1;
            sens_cfg[1].cfg.gyr.range = BMI2_GYR_RANGE_2000;
            sens_cfg[1].cfg.gyr.noise_perf = 1;
            acc_range_g_ = 2.0f;
            gyr_range_dps_ = 2000.0f;
            if (bmi2_set_sensor_config(sens_cfg, 2, &dev_) == BMI2_OK &&
                bmi2_sensor_enable(types, 2, &dev_) == BMI2_OK) {
                uint16_t fifo_cfg = BMI2_FIFO_HEADER_EN | BMI2_FIFO_TIME_EN | BMI2_FIFO_ACC_EN | BMI2_FIFO_GYR_EN;
                (void)bmi2_set_fifo_config(fifo_cfg, 1, &dev_);
                (void)bmi2_set_fifo_filter_data(BMI2_ACCEL, BMI2_FIFO_FILTERED_DATA, &dev_);
                (void)bmi2_set_fifo_filter_data(BMI2_GYRO, BMI2_FIFO_FILTERED_DATA, &dev_);
                (void)bmi2_set_fifo_down_sample(BMI2_ACCEL, 0, &dev_);
                (void)bmi2_set_fifo_down_sample(BMI2_GYRO, 0, &dev_);
                (void)bmi2_set_fifo_wm(64, &dev_);
                
                uint8_t pwr2 = 0, err2 = 0, stat2 = 0;
                (void)sread(0x7D, &pwr2, 1);
                (void)sread(0x02, &err2, 1);
                (void)sread(0x03, &stat2, 1);
                ESP_LOGI(TAG_BMI, "BMI2 path enabled: CHIP=0x%02X PWR=0x%02X ERR=0x%02X STAT=0x%02X", chip_id_, pwr2, err2, stat2);
                
                use_bmi2_path_ = true;
                inited_ = true;
                (void)EnableAnyMotion(40, 500);
                return true;
            }
    }
    
    ESP_LOGD(TAG_BMI, "Standard init failed (rslt=%d), trying manual fallback...", rslt);
    Reattach(50000, 0); // Use low speed for fallback

    auto check_err = [&](esp_err_t err, const char* msg) {
        if (err != ESP_OK) ESP_LOGE(TAG_BMI, "Manual fallback %s failed: %s", msg, esp_err_to_name(err));
        return err == ESP_OK;
    };

    // Manual fallback initialization sequence
    // 1. Soft reset
    uint8_t cmd_reset = 0xB6;
    check_err(i2c_master_transmit(i2c_device_, (uint8_t[]){0x7E, cmd_reset}, 2, 50), "SoftReset");
    BmiDelayUs(50000, nullptr);

    // 2. Disable power save
    uint8_t pwr_conf = 0x00;
    check_err(i2c_master_transmit(i2c_device_, (uint8_t[]){0x7C, pwr_conf}, 2, 50), "PwrConf");
    BmiDelayUs(10000, nullptr);
    // 2.1 Disable advanced power save if applicable
    check_err(i2c_master_transmit(i2c_device_, (uint8_t[]){0x7C, 0x00}, 2, 50), "PwrConf2");
    BmiDelayUs(10000, nullptr);

    // 2.5 Toggle initialization mode to ensure exit
    check_err(i2c_master_transmit(i2c_device_, (uint8_t[]){0x59, 0x01}, 2, 50), "InitCtrlStart");
    BmiDelayUs(30000, nullptr);
    check_err(i2c_master_transmit(i2c_device_, (uint8_t[]){0x59, 0x00}, 2, 50), "InitCtrlStop");
    BmiDelayUs(20000, nullptr);

    // 3. Enable Accel & Gyro manually（Prefer 0xA8 perf setting to match runtime）
    uint8_t w3[2] = {0x40, 0xA8}; 
    uint8_t w4[2] = {0x41, 0x00}; // Acc range +/- 2g
    uint8_t w5[2] = {0x42, 0xA8}; 
    uint8_t w6[2] = {0x43, 0x00}; // Gyr range 2000dps

    check_err(i2c_master_transmit(i2c_device_, w3, 2, 50), "AccConf");
    check_err(i2c_master_transmit(i2c_device_, w4, 2, 50), "AccRange");
    check_err(i2c_master_transmit(i2c_device_, w5, 2, 50), "GyrConf");
    check_err(i2c_master_transmit(i2c_device_, w6, 2, 50), "GyrRange");
    BmiDelayUs(10000, nullptr);

    // 4. PWR_CTRL (0x7D): Enable Aux(0), Gyr(1), Acc(1), Temp(0) -> 0x06
    uint8_t w7[2] = {0x7D, 0x06}; 
    check_err(i2c_master_transmit(i2c_device_, w7, 2, 50), "PwrCtrl");
    BmiDelayUs(50000, nullptr);

    // Wait for data ready
    uint8_t stat = 0;
    for(int i=0; i<40; i++) {
        (void)sread(0x1B, &stat, 1); // STATUS register
        if(stat & 0xC0) break; // Acc(bit7) or Gyr(bit6) DRDY
        BmiDelayUs(10000, nullptr);
    }

    uint8_t pwr = 0, err = 0;
    (void)sread(0x7D, &pwr, 1);
    (void)sread(0x02, &err, 1);
    (void)sread(0x1B, &stat, 1);
    ESP_LOGD(TAG_BMI, "BMI26x manual post-init: PWR=0x%02X ERR=0x%02X STAT=0x%02X", pwr, err, stat);

    // Verify power state: Acc(bit 2) and Gyr(bit 1) must be enabled (0x06)
    if ((pwr & 0x06) != 0x06) {
        ESP_LOGE(TAG_BMI, "Manual fallback failed: Sensors not enabled (PWR=0x%02X)", pwr);
        return false;
    }

    acc_range_g_ = 2.0f;
    gyr_range_dps_ = 2000.0f;
    use_bmi2_path_ = false;

    // Final accel enable to match runtime config and ensure data path
    (void)EnableAccel(0xA8, 0x00);

    // Probe data ready by reading ACC registers; if still zeros, try a short recovery.
    uint8_t accbuf[6]{};
    bool ready = false;
    for (int i = 0; i < 10; ++i) {
        if (ReadRaw(0x0C, accbuf, 6) && (accbuf[0]|accbuf[1]|accbuf[2]|accbuf[3]|accbuf[4]|accbuf[5])) { ready = true; break; }
        BmiDelayUs(10000, nullptr);
    }
    if (!ready) {
        // Toggle PWR_CTRL accel bit and reattach at lower speed to break bus stickiness
        uint8_t cur = ReadReg(0x7D);
        WriteReg(0x7D, (uint8_t)(cur & ~0x04));
        BmiDelayUs(10000, nullptr);
        WriteReg(0x7D, (uint8_t)(cur | 0x04));
        Reattach(50000, 10);
        BmiDelayUs(20000, nullptr);
        // Retry a short acc read
        if (ReadRaw(0x0C, accbuf, 6) && (accbuf[0]|accbuf[1]|accbuf[2]|accbuf[3]|accbuf[4]|accbuf[5])) {
            ready = true;
        }
    }
    if (!ready) {
        ESP_LOGD(TAG_BMI, "Manual fallback produced no accel data, will retry later");
        inited_ = false;
        return false;
    }
    inited_ = true;
    
    // Initialize dev structure just in case for fallback calls
    dev_.chip_id = chip_id_;
    dev_.read_write_len = 32;
    (void)EnableAnyMotion(40, 500);
    
    return true;
    }

bool BMI270Imu::EnableAccel(uint8_t acc_conf, uint8_t acc_range) {
    if (!inited_) return false;
    
    WriteReg(0x40, acc_conf);
    WriteReg(0x41, acc_range);
    vTaskDelay(pdMS_TO_TICKS(5));
    
    uint8_t pwr = ReadReg(0x7D);
    if ((pwr & 0x04) == 0 && (chip_id_ == 0x24 || chip_id_ == 0x26)) {
        WriteReg(0x7D, pwr | 0x04);
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    return true;
}

bool BMI270Imu::Reattach(uint32_t scl_hz, uint16_t scl_wait_us) {
    if (i2c_device_) {
        (void)i2c_master_bus_rm_device(i2c_device_);
        i2c_device_ = nullptr;
    }
    i2c_device_config_t cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = addr_,
        .scl_speed_hz = scl_hz,
        .scl_wait_us = scl_wait_us,
        .flags = {
            .disable_ack_check = 0,
        },
    };
    esp_err_t err = i2c_master_bus_add_device(bus_, &cfg, &i2c_device_);
    vTaskDelay(pdMS_TO_TICKS(2));
    if (err == ESP_OK) { current_scl_hz_ = scl_hz; current_scl_wait_us_ = scl_wait_us; }
    return err == ESP_OK;
}

bool BMI270Imu::EnsureEnabled() {
    if (!inited_) return false;
    if (use_bmi2_path_) {
        bmi2_sens_config sens_cfg[2];
        uint8_t types[2];
        types[0] = BMI2_ACCEL;
        types[1] = BMI2_GYRO;
        sens_cfg[0].type = BMI2_ACCEL;
        sens_cfg[0].cfg.acc.odr = BMI2_ACC_ODR_50HZ;
        sens_cfg[0].cfg.acc.bwp = 0x02;
        sens_cfg[0].cfg.acc.filter_perf = 1;
        sens_cfg[0].cfg.acc.range = BMI2_ACC_RANGE_2G;
        sens_cfg[1].type = BMI2_GYRO;
        sens_cfg[1].cfg.gyr.odr = BMI2_GYR_ODR_50HZ;
        sens_cfg[1].cfg.gyr.bwp = 0x02;
        sens_cfg[1].cfg.gyr.filter_perf = 1;
        sens_cfg[1].cfg.gyr.range = BMI2_GYR_RANGE_2000;
        sens_cfg[1].cfg.gyr.noise_perf = 1;
        acc_range_g_ = 2.0f;
        gyr_range_dps_ = 2000.0f;
        (void)bmi2_set_sensor_config(sens_cfg, 2, &dev_);
        (void)bmi2_sensor_enable(types, 2, &dev_);
        uint16_t fifo_cfg = BMI2_FIFO_HEADER_EN | BMI2_FIFO_TIME_EN | BMI2_FIFO_ACC_EN | BMI2_FIFO_GYR_EN;
        (void)bmi2_set_fifo_config(fifo_cfg, 1, &dev_);
        (void)bmi2_set_fifo_filter_data(BMI2_ACCEL, BMI2_FIFO_FILTERED_DATA, &dev_);
        (void)bmi2_set_fifo_filter_data(BMI2_GYRO, BMI2_FIFO_FILTERED_DATA, &dev_);
        (void)bmi2_set_fifo_down_sample(BMI2_ACCEL, 0, &dev_);
        (void)bmi2_set_fifo_down_sample(BMI2_GYRO, 0, &dev_);
        (void)bmi2_set_fifo_wm(64, &dev_);
        return true;
    } else {
        uint8_t wa[2] = {0x40, 0xA4};
        uint8_t wb[2] = {0x41, 0x00};
        (void)i2c_master_transmit(i2c_device_, wa, 2, 50);
        (void)i2c_master_transmit(i2c_device_, wb, 2, 50);
        vTaskDelay(pdMS_TO_TICKS(5));
        uint8_t pwr = ReadReg(0x7D);
        uint8_t wc[2] = {0x7D, (uint8_t)(pwr | 0x04)};
        (void)i2c_master_transmit(i2c_device_, wc, 2, 50);
        vTaskDelay(pdMS_TO_TICKS(5));
        return true;
    }
}

bool BMI270Imu::ReadRaw(uint8_t reg, uint8_t* buf, size_t len) {
    int64_t now_ms = esp_timer_get_time()/1000;
    if (s_i2c_driver_quiet_until_ms > now_ms) return false;
    if (!i2c_device_) return false;
    esp_err_t err = i2c_master_transmit_receive(i2c_device_, &reg, 1, buf, len, 1000);
    if (err == ESP_OK) return true;
    int s0 = gpio_get_level(AUDIO_CODEC_I2C_SCL_PIN);
    int d0 = gpio_get_level(AUDIO_CODEC_I2C_SDA_PIN);
    ESP_LOGE(TAG_BMI, "I2C read fail reg=0x%02X scl=%d sda=%d", reg, s0, d0);
    vTaskDelay(2);
    err = i2c_master_transmit_receive(i2c_device_, &reg, 1, buf, len, 1000);
    if (err == ESP_OK) return true;
    int s1 = gpio_get_level(AUDIO_CODEC_I2C_SCL_PIN);
    int d1 = gpio_get_level(AUDIO_CODEC_I2C_SDA_PIN);
    ESP_LOGE(TAG_BMI, "I2C read retry fail reg=0x%02X scl=%d sda=%d", reg, s1, d1);
    s_i2c_driver_quiet_until_ms = now_ms + 15000;
    return false;
}

bool BMI270Imu::ReadAccel(float &ax, float &ay, float &az) {
    if (!inited_) return false;
    if (!use_bmi2_path_) {
        uint8_t buf[6];
        // ACC registers start at 0x0C
        if (!ReadRaw(0x0C, buf, 6)) { 
            static int s_read_fail_cnt = 0;
            if ((++s_read_fail_cnt % 50) == 0) {
                ESP_LOGW(TAG_BMI, "ReadAccel failed (I2C).");
                uint8_t id = 0;
                if (ReadRaw(0x00, &id, 1)) ESP_LOGW(TAG_BMI, "Device ID check: 0x%02X", id);
                else ESP_LOGW(TAG_BMI, "Device ID check failed");
            }
            ax = 0; ay = 0; az = 0; return false; 
        }
        bool all_zero = (buf[0]|buf[1]|buf[2]|buf[3]|buf[4]|buf[5]) == 0;
        if (!all_zero) {
            int16_t x = (int16_t)bmi_u16_from_le(buf[0], buf[1]);
            int16_t y = (int16_t)bmi_u16_from_le(buf[2], buf[3]);
            int16_t z = (int16_t)bmi_u16_from_le(buf[4], buf[5]);
            ax = (float)x / 16384.0f;
            ay = (float)y / 16384.0f;
            az = (float)z / 16384.0f;
            return true;
        }
        // Inline attempt to enable BMI2 path if raw returns zeros persistently
        static int64_t s_last_inline_init_ms = 0;
        int64_t now_ms_inline = esp_timer_get_time()/1000;
        if (s_last_inline_init_ms == 0 || (now_ms_inline - s_last_inline_init_ms) > 5000) {
            int8_t init_rslt = bmi270_legacy_init(&dev_);
            if (init_rslt == BMI2_OK) {
                bmi2_sens_config sens_cfg[2];
                uint8_t types[2];
                types[0] = BMI2_ACCEL; types[1] = BMI2_GYRO;
                sens_cfg[0].type = BMI2_ACCEL;
                sens_cfg[0].cfg.acc.odr = BMI2_ACC_ODR_100HZ;
                sens_cfg[0].cfg.acc.bwp = 0x02;
                sens_cfg[0].cfg.acc.filter_perf = 1;
                sens_cfg[0].cfg.acc.range = BMI2_ACC_RANGE_2G;
                sens_cfg[1].type = BMI2_GYRO;
                sens_cfg[1].cfg.gyr.odr = BMI2_GYR_ODR_100HZ;
                sens_cfg[1].cfg.gyr.bwp = 0x02;
                sens_cfg[1].cfg.gyr.filter_perf = 1;
                sens_cfg[1].cfg.gyr.range = BMI2_GYR_RANGE_2000;
                sens_cfg[1].cfg.gyr.noise_perf = 1;
                acc_range_g_ = 2.0f;
                gyr_range_dps_ = 2000.0f;
                if (bmi2_set_sensor_config(sens_cfg, 2, &dev_) == BMI2_OK && bmi2_sensor_enable(types, 2, &dev_) == BMI2_OK) {
                    use_bmi2_path_ = true;
                }
            }
            s_last_inline_init_ms = now_ms_inline;
        }
        // Try BMI2 data path after inline init
        bmi2_sens_data data{};
        int8_t rslt = bmi2_get_sensor_data(&data, &dev_);
        if (rslt == BMI2_OK) {
            float scale = acc_range_g_ / 32768.0f;
            ax = (float)data.acc.x * scale;
            ay = (float)data.acc.y * scale;
            az = (float)data.acc.z * scale;
            return true;
        }
        static int s_zero_cnt = 0;
        if ((++s_zero_cnt % 100) == 0) {
            ESP_LOGW(TAG_BMI, "ReadAccel: All zeros and fallback failed (rslt=%d)", rslt);
        }
        ax = 0.0f; ay = 0.0f; az = 0.0f;
        return false;
    } else if (use_bmi2_path_) {
        bmi2_sens_data data{};
        int8_t rslt = bmi2_get_sensor_data(&data, &dev_);
        if (rslt == BMI2_OK) {
            float scale = acc_range_g_ / 32768.0f;
            ax = (float)data.acc.x * scale;
            ay = (float)data.acc.y * scale;
            az = (float)data.acc.z * scale;
            return true;
        }
        
        static int s_bmi2_fail_cnt = 0;
        if ((++s_bmi2_fail_cnt % 100) == 0) {
            ESP_LOGW(TAG_BMI, "ReadAccel BMI2 path failed (rslt=%d)", rslt);
        }

        bmi2_fifo_frame fifo{};
        uint8_t buf[256]{};
        fifo.data = buf;
        fifo.length = sizeof(buf);
        if (bmi2_read_fifo_data(&fifo, &dev_) != BMI2_OK) return false;
        bmi2_sens_axes_data acc{};
        uint16_t acc_len = 1;
        if (bmi2_extract_accel(&acc, &acc_len, &fifo, &dev_) != BMI2_OK || acc_len == 0) return false;
        float scale = acc_range_g_ / 32768.0f;
        ax = (float)acc.x * scale;
        ay = (float)acc.y * scale;
        az = (float)acc.z * scale;
        return true;
    } else {
        return false;
    }
}

bool BMI270Imu::ReadGyro(float &gx, float &gy, float &gz) {
    if (!inited_) return false;
    if (!use_bmi2_path_) {
        uint8_t buf[6];
        // GYR registers start at 0x12
        if (!ReadRaw(0x12, buf, 6)) { gx = 0; gy = 0; gz = 0; return false; }
        bool all_zero = (buf[0]|buf[1]|buf[2]|buf[3]|buf[4]|buf[5]) == 0;
        if (!all_zero) {
            int16_t x = (int16_t)bmi_u16_from_le(buf[0], buf[1]);
            int16_t y = (int16_t)bmi_u16_from_le(buf[2], buf[3]);
            int16_t z = (int16_t)bmi_u16_from_le(buf[4], buf[5]);
            float scale = 16.4f; // 2000dps scale
            gx = (float)x / scale;
            gy = (float)y / scale;
            gz = (float)z / scale;
            return true;
        }
        gx = 0.0f; gy = 0.0f; gz = 0.0f;
        return false;
    } else if (use_bmi2_path_) {
        bmi2_sens_data data{};
        int8_t rslt = bmi2_get_sensor_data(&data, &dev_);
        if (rslt == BMI2_OK) {
            float scale = gyr_range_dps_ / 32768.0f;
            gx = (float)data.gyr.x * scale;
            gy = (float)data.gyr.y * scale;
            gz = (float)data.gyr.z * scale;
            return true;
        }
        bmi2_fifo_frame fifo{};
        uint8_t buf[256]{};
        fifo.data = buf;
        fifo.length = sizeof(buf);
        if (bmi2_read_fifo_data(&fifo, &dev_) != BMI2_OK) return false;
        bmi2_sens_axes_data gyr{};
        uint16_t gyr_len = 1;
        if (bmi2_extract_gyro(&gyr, &gyr_len, &fifo, &dev_) != BMI2_OK || gyr_len == 0) return false;
        float scale = gyr_range_dps_ / 32768.0f;
        gx = (float)gyr.x * scale;
        gy = (float)gyr.y * scale;
        gz = (float)gyr.z * scale;
        return true;
    } else {
        return false;
    }
}

bool BMI270Imu::UpdateShakeDetection() {
    float ax, ay, az;
    if (!ReadAccel(ax, ay, az)) {
        return false;
    }

    // First sample
    if (last_ax_ == 0 && last_ay_ == 0 && last_az_ == 0) {
        last_ax_ = ax;
        last_ay_ = ay;
        last_az_ = az;
        return false;
    }

    float dx = fabs(ax - last_ax_);
    float dy = fabs(ay - last_ay_);
    float dz = fabs(az - last_az_);
    
    float diff = dx + dy + dz;
    
    shake_energy_ = shake_energy_ * 0.75f + diff;

    last_ax_ = ax;
    last_ay_ = ay;
    last_az_ = az;

    static int64_t s_win_ms = 0;
    int64_t now_ms = esp_timer_get_time()/1000;
    if (s_win_ms == 0 || (now_ms - s_win_ms) > 700) { s_win_ms = now_ms; shake_count_ = 0; }
    if (shake_energy_ > 0.35f) { shake_count_++; }
    if (shake_count_ >= 2) { shake_count_ = 0; shake_energy_ = 0; return true; }
    
    return false;
}

int8_t BMI270Imu::BmiRead(uint8_t reg, uint8_t* data, uint32_t len, void* intf_ptr) {
    auto* self = static_cast<BMI270Imu*>(intf_ptr);
    if (!self) return BMI2_E_NULL_PTR;
    if (!self->i2c_device_) return BMI2_E_COM_FAIL;
    int64_t now_ms = esp_timer_get_time()/1000;
    if (s_i2c_driver_quiet_until_ms > now_ms) return BMI2_E_COM_FAIL;
    
    esp_err_t err = i2c_master_transmit_receive(self->i2c_device_, &reg, 1, data, len, 1000);
    if (err != ESP_OK) {
        vTaskDelay(1);
        err = i2c_master_transmit_receive(self->i2c_device_, &reg, 1, data, len, 1000);
    }
    if (err != ESP_OK) { s_i2c_driver_quiet_until_ms = now_ms + 15000; }
    return (err == ESP_OK) ? BMI2_OK : BMI2_E_COM_FAIL;
}

int8_t BMI270Imu::BmiWrite(uint8_t reg, const uint8_t* data, uint32_t len, void* intf_ptr) {
    auto* self = static_cast<BMI270Imu*>(intf_ptr);
    if (!self) return BMI2_E_NULL_PTR;
    if (!self->i2c_device_) return BMI2_E_COM_FAIL;
    int64_t now_ms = esp_timer_get_time()/1000;
    if (s_i2c_driver_quiet_until_ms > now_ms) return BMI2_E_COM_FAIL;
    uint8_t buf[BMI2_MAX_BUFFER_SIZE + 1];
    if (len > BMI2_MAX_BUFFER_SIZE) return BMI2_E_OUT_OF_RANGE;
    buf[0] = reg;
    memcpy(buf + 1, data, len);
    
    esp_err_t err = i2c_master_transmit(self->i2c_device_, buf, len + 1, 1000);
    if (err != ESP_OK) {
        vTaskDelay(1);
        err = i2c_master_transmit(self->i2c_device_, buf, len + 1, 1000);
    }
    if (err != ESP_OK) { s_i2c_driver_quiet_until_ms = now_ms + 15000; }
    return (err == ESP_OK) ? BMI2_OK : BMI2_E_COM_FAIL;
}

void BMI270Imu::BmiDelayUs(uint32_t us, void* intf_ptr) {
    (void)intf_ptr;
    if (us >= 10000) {
        vTaskDelay(pdMS_TO_TICKS(us / 1000) + 1);
    } else {
        esp_rom_delay_us(us);
    }
}
bool BMI270Imu::EnableAnyMotion(uint16_t duration, uint16_t threshold) {
    if (!inited_) return false;
    bmi2_sens_config cfg{};
    cfg.type = BMI2_ANY_MOTION;
    cfg.cfg.any_motion.duration = duration;
    cfg.cfg.any_motion.threshold = threshold;
    cfg.cfg.any_motion.select_x = 1;
    cfg.cfg.any_motion.select_y = 1;
    cfg.cfg.any_motion.select_z = 1;
    (void)bmi2_set_sensor_config(&cfg, 1, &dev_);
    return true;
}

bool BMI270Imu::CheckAnyMotion() {
    if (!inited_) return false;
    uint16_t int_status = 0;
    int8_t rslt = bmi2_get_int_status(&int_status, &dev_);
    // BMI2_ANY_MOTION_INT is bit 6 of INT_STATUS_0
    return (rslt == BMI2_OK) && ((int_status & (1 << 6)) != 0);
}

bool BMI270Imu::CheckAnyMotionRaw() {
    uint8_t ist = 0;
    if (!ReadRaw(0x1C, &ist, 1)) return false; // INT_STATUS_0
    return (ist & 0x40) != 0;
}

bool BMI270Imu::ForcePresent() {
    chip_id_ = 0x26; // assume BMI26x based on WHOAMI
    use_bmi2_path_ = false;
    acc_range_g_ = 2.0f;
    gyr_range_dps_ = 2000.0f;
    inited_ = true;
    return true;
}

bool BMI270Imu::TryRecoverData() {
    if (!i2c_device_) {
        if (!Reattach(10000, 30)) return false;
    } else {
        (void)Reattach(10000, 30);
    }
    // Reconfigure BMI2 device interface for potential legacy init
    dev_.intf = BMI2_I2C_INTF;
    dev_.intf_ptr = this;
    dev_.read = &BMI270Imu::BmiRead;
    dev_.write = &BMI270Imu::BmiWrite;
    dev_.delay_us = &BMI270Imu::BmiDelayUs;
    dev_.read_write_len = 32;
    dev_.chip_id = 0x26;
    // Soft reset and exit init mode
    WriteReg(0x7E, 0xB6);
    vTaskDelay(pdMS_TO_TICKS(100));
    WriteReg(0x59, 0x01);
    vTaskDelay(pdMS_TO_TICKS(30));
    WriteReg(0x59, 0x00);
    vTaskDelay(pdMS_TO_TICKS(20));
    // Disable advanced power save
    WriteReg(0x7C, 0x00);
    vTaskDelay(pdMS_TO_TICKS(20));
    // Toggle accel enable and re-config
    uint8_t pwr = ReadReg(0x7D);
    WriteReg(0x7D, (uint8_t)(pwr & ~0x04));
    vTaskDelay(pdMS_TO_TICKS(150));
    WriteReg(0x40, 0xA8);
    WriteReg(0x41, 0x00);
    WriteReg(0x7D, (uint8_t)((ReadReg(0x7D)) | 0x04));
    vTaskDelay(pdMS_TO_TICKS(200));
    // Enable gyro as well
    WriteReg(0x42, 0xA8);
    WriteReg(0x43, 0x00);
    // Try multiple raw reads
    uint8_t buf[6]{};
    for (int i = 0; i < 12; ++i) {
        if (ReadRaw(0x0C, buf, 6) && (buf[0]|buf[1]|buf[2]|buf[3]|buf[4]|buf[5])) {
            int16_t x = (int16_t)bmi_u16_from_le(buf[0], buf[1]);
            int16_t y = (int16_t)bmi_u16_from_le(buf[2], buf[3]);
            int16_t z = (int16_t)bmi_u16_from_le(buf[4], buf[5]);
            (void)x; (void)y; (void)z;
            inited_ = true; use_bmi2_path_ = false; return true;
        }
        vTaskDelay(pdMS_TO_TICKS(60));
    }
    // Try data-ready poll
    uint8_t stat = 0;
    for (int k = 0; k < 60; ++k) {
        if (ReadRaw(0x1B, &stat, 1) && (stat & 0xC0)) { inited_ = true; use_bmi2_path_ = false; return true; }
        vTaskDelay(pdMS_TO_TICKS(40));

    (void)Reattach(10000, 30);
    uint8_t pwr2 = ReadReg(0x7D);
    WriteReg(0x7D, (uint8_t)(pwr2 & ~0x04));
    vTaskDelay(pdMS_TO_TICKS(150));
    WriteReg(0x40, 0xA8);
    WriteReg(0x41, 0x00);
    WriteReg(0x7D, (uint8_t)((ReadReg(0x7D)) | 0x04));
    vTaskDelay(pdMS_TO_TICKS(200));
    uint8_t buf2[6]{};
    for (int j = 0; j < 8; ++j) {
        if (ReadRaw(0x0C, buf2, 6) && (buf2[0]|buf2[1]|buf2[2]|buf2[3]|buf2[4]|buf2[5])) { inited_ = true; use_bmi2_path_ = false; return true; }
        vTaskDelay(pdMS_TO_TICKS(60));
    }
    }
    // Attempt BMI2 legacy init after raw recovery attempts
    int8_t init_rslt = bmi270_legacy_init(&dev_);
    if (init_rslt == BMI2_OK) {
        bmi2_sens_config sens_cfg[2];
        uint8_t types[2];
        types[0] = BMI2_ACCEL; types[1] = BMI2_GYRO;
        sens_cfg[0].type = BMI2_ACCEL;
        sens_cfg[0].cfg.acc.odr = BMI2_ACC_ODR_100HZ;
        sens_cfg[0].cfg.acc.bwp = 0x02;
        sens_cfg[0].cfg.acc.filter_perf = 1;
        sens_cfg[0].cfg.acc.range = BMI2_ACC_RANGE_2G;
        sens_cfg[1].type = BMI2_GYRO;
        sens_cfg[1].cfg.gyr.odr = BMI2_GYR_ODR_100HZ;
        sens_cfg[1].cfg.gyr.bwp = 0x02;
        sens_cfg[1].cfg.gyr.filter_perf = 1;
        sens_cfg[1].cfg.gyr.range = BMI2_GYR_RANGE_2000;
        sens_cfg[1].cfg.gyr.noise_perf = 1;
        acc_range_g_ = 2.0f;
        gyr_range_dps_ = 2000.0f;
        if (bmi2_set_sensor_config(sens_cfg, 2, &dev_) == BMI2_OK && bmi2_sensor_enable(types, 2, &dev_) == BMI2_OK) {
            use_bmi2_path_ = true;
            inited_ = true;
            (void)EnableAnyMotion(40, 500);
            return true;
        }
    }
    return false;
}
bool BMI270Imu::SetDriverQuiet(int64_t until_ms) {
    s_i2c_driver_quiet_until_ms = until_ms;
    return true;
}
