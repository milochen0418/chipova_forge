# The chip's clock: 25 MHz. SPI's sclk is sampled by clk (at most clk / 8).
create_clock -name clk -period 40.0 [get_ports clk]
set_input_delay 5.0 -clock clk [get_ports {rst_n spi_sclk spi_mosi spi_cs_n}]
set_output_delay 5.0 -clock clk [get_ports {spi_miso irq alarm_led}]
