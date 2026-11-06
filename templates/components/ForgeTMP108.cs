//
// Chipova Forge: a TMP108-compatible digital temperature sensor for Renode.
//
// Renode 1.17.0's own TMP108 is a stub with whole degrees; this one follows the TMP108 data
// sheet for what a firmware reads: the temperature register (pointer 0) holds 12 bits,
// left-justified, 0.0625 degC per step, two's complement; the configuration and limit registers
// (pointers 1-3) read and write as plain 16-bit values. It returns the bytes of one transfer in
// order however the I2C controller asks for them (one byte per call or several).
//
// Forge sets the temperature in thousandths of a degree (MilliCelsius, an integer: the monitor
// would cut a decimal to whole degrees), and can ask how many times the firmware read it.
//
using System;
using Antmicro.Renode.Core;
using Antmicro.Renode.Logging;
using Antmicro.Renode.Peripherals.I2C;

namespace Antmicro.Renode.Peripherals.Sensors
{
    public class ForgeTMP108 : II2CPeripheral
    {
        public ForgeTMP108(IMachine machine)
        {
            Reset();
        }

        public void Write(byte[] data)
        {
            if(data.Length == 0)
            {
                return;
            }
            pointer = data[0] & 0x3;
            offset = 0;
            if(data.Length >= 3 && pointer != 0)
            {
                registers[pointer] = (ushort)((data[1] << 8) | data[2]);
            }
        }

        public byte[] Read(int count)
        {
            var result = new byte[count];
            var value = pointer == 0 ? TemperatureRegister : registers[pointer];
            for(var i = 0; i < count; i++)
            {
                result[i] = (offset % 2) == 0 ? (byte)(value >> 8) : (byte)value;
                offset++;
            }
            if(pointer == 0)
            {
                Reads++;
            }
            return result;
        }

        public void FinishTransmission()
        {
            offset = 0;
        }

        public void Reset()
        {
            pointer = 0;
            offset = 0;
            MilliCelsius = 25000;
            Reads = 0;
            registers[1] = 0x1022; // configuration: continuous conversion, 1 Hz
            registers[2] = 0x8000; // T low
            registers[3] = 0x7FF0; // T high
        }

        public int MilliCelsius
        {
            get => milliCelsius;
            set => milliCelsius = Math.Max(-40000, Math.Min(125000, value));
        }

        public long Reads { get; private set; }

        private ushort TemperatureRegister
        {
            get
            {
                var steps = (int)Math.Round(milliCelsius / 62.5);
                return (ushort)((steps & 0xFFF) << 4);
            }
        }

        private readonly ushort[] registers = new ushort[4];
        private int milliCelsius;
        private int pointer;
        private int offset;
    }
}
