`timescale 1ns / 1ps
// Checks that the heartbeat pulses exactly once every 2^N clocks, one clock long.
module tb_heartbeat;
    localparam N = 4;
    localparam PERIOD = 1 << N;

    reg  clk = 1'b0;
    reg  nreset = 1'b0;
    wire out;

    heartbeat #(.N(N)) dut (.clk(clk), .nreset(nreset), .out(out));

    always #5 clk = ~clk;

    integer cycle = 0;
    integer last_pulse = -1;
    integer pulses = 0;
    integer errors = 0;

    always @(posedge clk) begin
        cycle <= cycle + 1;
        if (out) begin
            if (last_pulse >= 0 && cycle - last_pulse != PERIOD) begin
                $display("%0t: ERROR pulse after %0d clocks, expected %0d", $time, cycle - last_pulse, PERIOD);
                errors = errors + 1;
            end
            $display("%0t: pulse %0d at clock %0d", $time, pulses, cycle);
            last_pulse = cycle;
            pulses = pulses + 1;
        end
    end

    initial begin
        $dumpfile("wave.vcd");
        $dumpvars(0, tb_heartbeat);
        #23 nreset = 1'b1;
        repeat (3 * PERIOD + 4) @(posedge clk);
        if (pulses != 3) begin
            $display("ERROR expected 3 pulses, saw %0d", pulses);
            errors = errors + 1;
        end
        if (errors == 0) $display("PASS heartbeat: %0d pulses, every %0d clocks", pulses, PERIOD);
        else $display("FAIL heartbeat: %0d errors", errors);
        $finish;
    end
endmodule
