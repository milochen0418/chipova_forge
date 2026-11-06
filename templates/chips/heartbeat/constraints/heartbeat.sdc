# 100 MHz clock on clk; inputs and outputs sampled on the same clock.
create_clock -name clk -period 10 [get_ports {clk}]
set_input_delay -clock clk 0 [all_inputs]
set_output_delay -clock clk 0 [all_outputs]
