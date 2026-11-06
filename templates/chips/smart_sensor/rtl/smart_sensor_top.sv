// The smart sensor chip: the MCU writes the temperature it measured and a threshold over SPI;
// the chip compares them (with hysteresis), counts alarms, lights its alarm pin while the
// temperature is above the threshold and raises its IRQ pin while an alarm is pending and
// interrupts are on.
//
// Registers: smart_sensor_regs, generated from registers/smart_sensor.rdl by PeakRDL-regblock,
// reached through spi_apb_bridge (see its header for the SPI frames).
module smart_sensor_top (
    input  wire clk,
    input  wire rst_n,
    // SPI (the MCU is the master), mode 0
    input  wire spi_sclk,
    input  wire spi_mosi,
    output wire spi_miso,
    input  wire spi_cs_n,
    // Pins
    output wire irq,
    output wire alarm_led
);
    import smart_sensor_regs_pkg::*;

    wire        psel, penable, pwrite, pready, pslverr;
    wire [4:0]  paddr;
    wire [31:0] pwdata, prdata;

    spi_apb_bridge #(.ADDR_WIDTH(5)) bridge (
        .clk     (clk),
        .rst_n   (rst_n),
        .sclk    (spi_sclk),
        .mosi    (spi_mosi),
        .miso    (spi_miso),
        .cs_n    (spi_cs_n),
        .psel    (psel),
        .penable (penable),
        .pwrite  (pwrite),
        .paddr   (paddr),
        .pwdata  (pwdata),
        .pready  (pready),
        .prdata  (prdata),
        .pslverr (pslverr)
    );

    smart_sensor_regs__in_t  hwif_in;
    smart_sensor_regs__out_t hwif_out;

    smart_sensor_regs regs (
        .clk           (clk),
        .rst           (~rst_n),
        .s_apb_psel    (psel),
        .s_apb_penable (penable),
        .s_apb_pwrite  (pwrite),
        .s_apb_paddr   (paddr),
        .s_apb_pwdata  (pwdata),
        .s_apb_pready  (pready),
        .s_apb_prdata  (prdata),
        .s_apb_pslverr (pslverr),
        .hwif_in       (hwif_in),
        .hwif_out      (hwif_out)
    );

    wire               enabled   = hwif_out.CTRL.EN.value;
    wire signed [16:0] temp      = {hwif_out.TEMP.VALUE.value[15], hwif_out.TEMP.VALUE.value};
    wire signed [16:0] threshold = {hwif_out.THRESHOLD.VALUE.value[15], hwif_out.THRESHOLD.VALUE.value};
    wire signed [16:0] hyst      = {1'b0, hwif_out.HYST.VALUE.value};

    reg        above;
    reg [15:0] count;

    // Once above, the alarm ends only below (threshold - hysteresis): no chatter at the edge.
    wire next_above = enabled && (above ? (temp > threshold - hyst) : (temp > threshold));
    wire rising     = next_above && !above;

    always @(posedge clk) begin
        if (!rst_n) begin
            above <= 1'b0;
            count <= 16'd0;
        end else begin
            above <= next_above;
            if (rising) count <= count + 16'd1;
        end
    end

    assign hwif_in.STATUS.ABOVE.next  = above;
    assign hwif_in.STATUS.ALARM.hwset = rising;
    assign hwif_in.COUNT.VALUE.next   = count;

    assign irq       = hwif_out.CTRL.IRQ_EN.value && hwif_out.STATUS.ALARM.value;
    assign alarm_led = above;
endmodule
