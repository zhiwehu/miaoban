idf_component_register(SRCS "main.cc" INCLUDE_DIRS "." "../../shared" REQUIRES esp_driver_gpio esp_driver_ledc esp_driver_uart esp_adc esp_timer esp_driver_usb_serial_jtag vfs)
