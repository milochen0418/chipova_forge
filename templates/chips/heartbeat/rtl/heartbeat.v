// Heartbeat: `out` pulses high for one clock every 2^N clocks.
module heartbeat #(
    parameter N = 8
) (
    input  wire clk,     // clock
    input  wire nreset,  // asynchronous reset, active low
    output reg  out      // the heartbeat
);

    reg [N-1:0] count;

    always @(posedge clk or negedge nreset) begin
        if (!nreset) begin
            count <= {N{1'b0}};
            out   <= 1'b0;
        end else begin
            count <= count + 1'b1;
            out   <= (count == {N{1'b1}});
        end
    end

endmodule
