// Self-checking testbench of the smart sensor chip: SPI writes and reads of its registers, the
// comparison with hysteresis, the alarm count, the IRQ and alarm pins. Prints PASS or FAIL lines.
`timescale 1ns/1ps
module tb_smart_sensor;
    reg clk = 0, rst_n = 0;
    reg sclk = 0, mosi = 0, cs_n = 1;
    wire miso, irq, alarm_led;
    integer errors = 0;

    smart_sensor_top dut (
        .clk(clk), .rst_n(rst_n),
        .spi_sclk(sclk), .spi_mosi(mosi), .spi_miso(miso), .spi_cs_n(cs_n),
        .irq(irq), .alarm_led(alarm_led)
    );

    always #20 clk = ~clk;  // 25 MHz

    // One byte, mode 0: data out before the rising edge, sampled on it.
    task automatic xfer(input [7:0] tx, output [7:0] rx);
        integer i;
        begin
            rx = 0;
            for (i = 7; i >= 0; i = i - 1) begin
                mosi = tx[i];
                #320;
                rx = {rx[6:0], miso};
                sclk = 1;
                #320;
                sclk = 0;
            end
            #320;
        end
    endtask

    task automatic reg_write(input [6:0] index, input [31:0] value);
        reg [7:0] r;
        begin
            cs_n = 0; #320;
            xfer({1'b1, index}, r);
            xfer(value[31:24], r); xfer(value[23:16], r); xfer(value[15:8], r); xfer(value[7:0], r);
            cs_n = 1; #640;
        end
    endtask

    task automatic reg_read(input [6:0] index, output [31:0] value);
        reg [7:0] b0, b1, b2, b3, r;
        begin
            cs_n = 0; #320;
            xfer({1'b0, index}, r);
            xfer(8'h00, b0); xfer(8'h00, b1); xfer(8'h00, b2); xfer(8'h00, b3);
            cs_n = 1; #640;
            value = {b0, b1, b2, b3};
        end
    endtask

    task automatic expect32(input [255:0] what, input [31:0] got, input [31:0] want);
        begin
            if (got !== want) begin
                $display("FAIL %0s: got 0x%08x, want 0x%08x", what, got, want);
                errors = errors + 1;
            end else begin
                $display("ok   %0s = 0x%08x", what, got);
            end
        end
    endtask

    reg [31:0] v;
    initial begin
        if ($test$plusargs("vcd")) begin
            $dumpfile("tb_smart_sensor.vcd");
            $dumpvars(0, tb_smart_sensor);
        end
        #200 rst_n = 1;
        #400;
        reg_read(0, v);  expect32("ID", v, 32'h534E5331);
        reg_read(2, v);  expect32("THRESHOLD after reset", v, 32'd3000);
        reg_read(5, v);  expect32("HYST after reset", v, 32'd50);
        reg_write(2, 32'd3500);
        reg_read(2, v);  expect32("THRESHOLD written", v, 32'd3500);
        reg_write(1, 32'd3);                     // EN | IRQ_EN
        reg_write(3, 32'd2500);                  // 25.00 degC
        reg_read(4, v);  expect32("STATUS at 25.00", v, 32'd0);
        if (irq !== 1'b0 || alarm_led !== 1'b0) begin $display("FAIL pins at 25.00"); errors = errors + 1; end
        reg_write(3, 32'd4000);                  // 40.00 degC
        #200;
        reg_read(4, v);  expect32("STATUS at 40.00", v, 32'd3);
        if (irq !== 1'b1 || alarm_led !== 1'b1) begin $display("FAIL pins at 40.00: irq=%b led=%b", irq, alarm_led); errors = errors + 1; end
        reg_read(6, v);  expect32("COUNT", v, 32'd1);
        reg_write(4, 32'd2);                     // clear ALARM (write 1)
        reg_read(4, v);  expect32("STATUS after clear", v, 32'd1);
        if (irq !== 1'b0) begin $display("FAIL irq after clear"); errors = errors + 1; end
        reg_write(3, 32'd3480);                  // 34.80: inside the hysteresis, still above
        reg_read(4, v);  expect32("STATUS at 34.80 (hysteresis)", v, 32'd1);
        reg_write(3, 32'd3400);                  // 34.00: below 35.00 - 0.50
        reg_read(4, v);  expect32("STATUS at 34.00", v, 32'd0);
        reg_write(3, 32'hFFFF_FDF5);             // -5.25 degC (16-bit two's complement: 0xFDF5)
        reg_read(3, v);  expect32("TEMP negative", v, 32'h0000_FDF5);
        reg_read(4, v);  expect32("STATUS at -5.25", v, 32'd0);
        if (errors == 0) $display("PASS smart_sensor: SPI registers, comparison, hysteresis, alarm, IRQ");
        else $display("FAIL smart_sensor: %0d errors", errors);
        $finish;
    end
endmodule
