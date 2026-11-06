// SPI (mode 0, most significant bit first) to APB3: how the MCU reaches the chip's registers
// over four wires.
//
// A frame is what happens while cs_n is low: a command byte, then four data bytes.
//   command  bit 7 = 1 to write, 0 to read; bits 6..0 = the register's index (its offset / 4)
//   write    the four bytes are the value, most significant first; the register is written
//            after the last one
//   read     the register is read right after the command byte, and its value comes out on
//            miso during the four bytes that follow (the MCU sends anything meanwhile)
//
// sclk, mosi and cs_n are sampled with clk through two flip-flops each, so sclk must be at
// most clk / 8 (the data sheet's limit; Forge's simulation drives it at clk / 16).
module spi_apb_bridge #(
    parameter ADDR_WIDTH = 8
) (
    input  wire                  clk,
    input  wire                  rst_n,
    // SPI (the MCU is the master)
    input  wire                  sclk,
    input  wire                  mosi,
    output wire                  miso,
    input  wire                  cs_n,
    // APB3 (this bridge is the master)
    output reg                   psel,
    output reg                   penable,
    output reg                   pwrite,
    output reg  [ADDR_WIDTH-1:0] paddr,
    output reg  [31:0]           pwdata,
    input  wire                  pready,
    input  wire [31:0]           prdata,
    input  wire                  pslverr
);
    // ---------------------------------------------------------------- the pins, in clk's time
    reg [2:0] sclk_q;
    reg [2:0] cs_q;
    reg [1:0] mosi_q;
    always @(posedge clk) begin
        if (!rst_n) begin
            sclk_q <= 3'b000;
            cs_q   <= 3'b111;
            mosi_q <= 2'b00;
        end else begin
            sclk_q <= {sclk_q[1:0], sclk};
            cs_q   <= {cs_q[1:0], cs_n};
            mosi_q <= {mosi_q[0], mosi};
        end
    end
    wire selected    = !cs_q[1];
    wire frame_start = cs_q[2:1] == 2'b10;
    wire sclk_rise   = sclk_q[2:1] == 2'b01;
    wire sclk_fall   = sclk_q[2:1] == 2'b10;

    // ---------------------------------------------------------------- the frame
    reg [2:0]  bit_count;
    reg [2:0]  byte_count;    // bytes received in this frame (stops at 5)
    reg [6:0]  shift_in;
    reg [7:0]  command;
    reg [31:0] data_in;
    reg [31:0] data_out;
    reg        miso_q;
    reg        start_read;
    reg        start_write;
    wire [7:0] byte_in = {shift_in, mosi_q[1]};
    assign miso = miso_q;

    // ---------------------------------------------------------------- the APB master
    localparam IDLE = 2'd0, SETUP = 2'd1, ACCESS = 2'd2;
    reg [1:0] state;

    always @(posedge clk) begin
        if (!rst_n) begin
            bit_count   <= 3'd0;
            byte_count  <= 3'd0;
            shift_in    <= 7'd0;
            command     <= 8'd0;
            data_in     <= 32'd0;
            data_out    <= 32'd0;
            miso_q      <= 1'b0;
            start_read  <= 1'b0;
            start_write <= 1'b0;
            state       <= IDLE;
            psel        <= 1'b0;
            penable     <= 1'b0;
            pwrite      <= 1'b0;
            paddr       <= {ADDR_WIDTH{1'b0}};
            pwdata      <= 32'd0;
        end else begin
            start_read  <= 1'b0;
            start_write <= 1'b0;

            if (!selected || frame_start) begin
                bit_count  <= 3'd0;
                byte_count <= 3'd0;
                if (frame_start) begin
                    miso_q   <= 1'b0;
                    data_out <= 32'd0;
                end
            end else begin
                if (sclk_rise) begin
                    shift_in  <= byte_in[6:0];
                    bit_count <= bit_count + 3'd1;
                    if (bit_count == 3'd7) begin
                        if (byte_count == 3'd0) begin
                            command <= byte_in;
                            if (!byte_in[7]) start_read <= 1'b1;
                        end else begin
                            data_in <= {data_in[23:0], byte_in};
                            if (byte_count == 3'd4 && command[7]) start_write <= 1'b1;
                        end
                        if (byte_count != 3'd5) byte_count <= byte_count + 3'd1;
                    end
                end
                if (sclk_fall && byte_count != 3'd0 && !command[7]) begin
                    miso_q   <= data_out[31];
                    data_out <= {data_out[30:0], 1'b0};
                end
            end

            case (state)
                IDLE: begin
                    if (start_read || start_write) begin
                        psel    <= 1'b1;
                        penable <= 1'b0;
                        pwrite  <= start_write;
                        paddr   <= {command[6:0], 2'b00};
                        pwdata  <= data_in;
                        state   <= SETUP;
                    end
                end
                SETUP: begin
                    penable <= 1'b1;
                    state   <= ACCESS;
                end
                default: begin
                    if (pready) begin
                        psel    <= 1'b0;
                        penable <= 1'b0;
                        state   <= IDLE;
                        if (!pwrite) data_out <= pslverr ? 32'hDEAD_BEEF : prdata;
                    end
                end
            endcase
        end
    end
endmodule
