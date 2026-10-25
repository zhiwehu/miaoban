#ifndef BMI270_IMU_H
#define BMI270_IMU_H

#include <stdint.h>
#include "i2c_device.h"
#include "bmi2.h"
#include "bmi270.h"
#include "bmi270_context.h"
#include "bmi270_legacy.h"

class BMI270Imu : public I2cDevice {
public:
    explicit BMI270Imu(i2c_master_bus_handle_t bus, uint8_t addr)
        : I2cDevice(bus, addr), bus_(bus), addr_(addr) {}

    void SetAddress(uint8_t addr) { addr_ = addr; }
    bool Initialize();
    bool ReadAccel(float &ax, float &ay, float &az);
    bool ReadGyro(float &gx, float &gy, float &gz);
    bool EnableAccel(uint8_t acc_conf, uint8_t acc_range);
    bool ReadRaw(uint8_t reg, uint8_t* buf, size_t len);
    bool EnsureEnabled();
    bool Reattach(uint32_t scl_hz, uint16_t scl_wait_us);
    bool EnableAnyMotion(uint16_t duration, uint16_t threshold);
    bool CheckAnyMotion();
    bool ForcePresent();
    bool TryRecoverData();
    bool CheckAnyMotionRaw();
    bool SetDriverQuiet(int64_t until_ms);

    // New method for shake detection
    // Returns true if shake is detected.
    // Should be called frequently (e.g., > 20Hz)
    bool UpdateShakeDetection();

private:
    bool inited_ = false;
    uint8_t chip_id_ = 0;
    bmi2_dev dev_{};
    float acc_range_g_ = 2.0f;
    float gyr_range_dps_ = 2000.0f;
    bool use_bmi2_path_ = false;
    i2c_master_bus_handle_t bus_;
    uint8_t addr_;
    uint8_t anymot_enabled_ = 0;
    uint32_t current_scl_hz_ = 50000;
    uint16_t current_scl_wait_us_ = 30;

    // Shake detection state
    float last_ax_ = 0, last_ay_ = 0, last_az_ = 0;
    float shake_energy_ = 0;
    int shake_count_ = 0;

    static int8_t BmiRead(uint8_t reg, uint8_t* data, uint32_t len, void* intf_ptr);
    static int8_t BmiWrite(uint8_t reg, const uint8_t* data, uint32_t len, void* intf_ptr);
    static void BmiDelayUs(uint32_t us, void* intf_ptr);
};

#endif // BMI270_IMU_H
