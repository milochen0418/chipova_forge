//
// Chipova Forge: a chip on an SPI bus, simulated by its own RTL (Verilator), for Renode.
//
// Renode's co-simulation reaches HDL models through a memory bus (APB3, AXI, Wishbone); a chip
// with an SPI interface has four pins instead. This peripheral sits on the MCU's SPI controller
// (`chip: CoSimulated.ForgeSpiChip @ spi1`) and passes each byte the controller sends to the chip
// library, which drives the chip's sclk and mosi pins bit by bit in the RTL and returns what its
// miso pin said (SPI mode 0, most significant bit first). The chip's select pin is an ordinary
// GPIO line from the MCU (`gpioPortA: 4 -> chip@0`), as on the board.
//
// On the library's side (Forge's generated sim_main.cpp) a byte read at offset 0x00..0xFF means
// "transfer this byte"; nothing else uses byte reads there.
//
// Renode compiles this file when it is included; the co-simulation plugin must be loaded first
// (hooks.py does: clr.AddReference("CoSimulationPlugin")).
//
// For people and tools looking at the chip (Forge's register panel), DebugReadRegister and
// DebugWriteRegister run a whole register frame of Forge's SPI-to-APB bridge themselves, with the
// chip's select pulled low for them, when the MCU isn't in the middle of a frame.
//
using System;
using Antmicro.Renode.Core;
using Antmicro.Renode.Exceptions;
using Antmicro.Renode.Logging;
using Antmicro.Renode.Peripherals.SPI;

namespace Antmicro.Renode.Peripherals.CoSimulated
{
    public class ForgeSpiChip : CoSimulatedPeripheral, ISPIPeripheral, IGPIOReceiver
    {
        public ForgeSpiChip(Machine machine, ulong frequency = 1000000000, ulong limitBuffer = 1000000,
            int timeout = 3000, Antmicro.Renode.Core.Range? cosimToRenodeSignalRange = null, int chipSelectInput = 0)
            : base(machine, frequency: frequency, limitBuffer: limitBuffer, timeout: timeout,
                   cosimToRenodeSignalRange: cosimToRenodeSignalRange)
        {
            this.chipSelectInput = chipSelectInput;
            mcuSelect = false;
        }

        public byte Transmit(byte data)
        {
            Transfers++;
            return ReadByte(data);
        }

        public void FinishTransmission()
        {
            // The chip select is a GPIO line: the MCU's firmware drives it.
        }

        public new void OnGPIO(int number, bool value)
        {
            if(number == chipSelectInput)
            {
                // Active low: low = the MCU has the chip selected.
                mcuSelect = !value;
            }
            base.OnGPIO(number, value);
        }

        public uint DebugReadRegister(int index)
        {
            Debug(index);
            base.OnGPIO(chipSelectInput, false);
            ReadByte((byte)(index & 0x7F));
            uint value = 0;
            for(var i = 0; i < 4; i++)
            {
                value = (value << 8) | ReadByte(0);
            }
            base.OnGPIO(chipSelectInput, true);
            return value;
        }

        public void DebugWriteRegister(int index, uint value)
        {
            Debug(index);
            base.OnGPIO(chipSelectInput, false);
            ReadByte((byte)(0x80 | (index & 0x7F)));
            for(var i = 3; i >= 0; i--)
            {
                ReadByte((byte)(value >> (8 * i)));
            }
            base.OnGPIO(chipSelectInput, true);
        }

        public long Transfers { get; private set; }

        public bool McuSelected => mcuSelect;

        private void Debug(int index)
        {
            if(mcuSelect)
            {
                throw new RecoverableException("the MCU is in the middle of an SPI frame: try again");
            }
            if(index < 0 || index > 0x7F)
            {
                throw new RecoverableException("register index 0..127");
            }
        }

        private readonly int chipSelectInput;
        private bool mcuSelect;
    }
}
