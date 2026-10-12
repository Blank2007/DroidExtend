// Does a plain, non-PyInstaller .exe get to change display settings on this box?
// Compiled with the in-box csc.exe; mirrors exactly what host/win_display.py does.
using System;
using System.IO;
using System.Runtime.InteropServices;

public class DispProbe
{
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct DEVMODE
    {
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmDeviceName;
        public ushort dmSpecVersion, dmDriverVersion, dmSize, dmDriverExtra;
        public uint dmFields;
        public int dmPositionX, dmPositionY;
        public uint dmDisplayOrientation, dmDisplayFixedOutput;
        public short dmColor, dmDuplex, dmYResolution, dmTTOption, dmCollate;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmFormName;
        public ushort dmLogPixels;
        public uint dmBitsPerPel, dmPelsWidth, dmPelsHeight, dmDisplayFlags, dmDisplayFrequency;
        public uint dmICMMethod, dmICMIntent, dmMediaType, dmDitherType,
                    dmReserved1, dmReserved2, dmPanningWidth, dmPanningHeight;
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern int EnumDisplaySettingsEx(string dev, int mode, ref DEVMODE dm, int flags);
    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern int ChangeDisplaySettingsEx(string dev, ref DEVMODE dm, IntPtr hwnd,
                                                     uint flags, IntPtr param);
    [DllImport("user32.dll", SetLastError = true)]
    public static extern int SetDisplayConfig(uint n, IntPtr paths, uint m, IntPtr modes, uint flags);

    const uint DM_PELSWIDTH = 0x00080000, DM_PELSHEIGHT = 0x00100000, DM_DISPLAYFREQUENCY = 0x00400000;
    const uint CDS_TEST = 0x2, CDS_UPDATEREGISTRY = 0x1;
    const uint SDC_TOPOLOGY_EXTEND = 0x4, SDC_VALIDATE = 0x40, SDC_APPLY = 0x80;

    static StreamWriter log;

    static void Say(string s)
    {
        Console.WriteLine(s);
        log.WriteLine(s);
        log.Flush();
    }

    public static int Main(string[] args)
    {
        // Default next to the exe so the probe has no opinion about where the
        // repo lives; pass a path as the second argument to override.
        string outPath = args.Length > 1
            ? args[1]
            : Path.Combine(AppContext.BaseDirectory, "cs_probe.txt");
        log = new StreamWriter(outPath, false, System.Text.Encoding.UTF8);
        Say("exe = " + System.Reflection.Assembly.GetExecutingAssembly().Location);
        Say("sizeof DEVMODE = " + Marshal.SizeOf(typeof(DEVMODE)));

        string dev = args.Length > 0 ? args[0] : @"\\.\DISPLAY20";
        DEVMODE dm = new DEVMODE();
        dm.dmSize = (ushort)Marshal.SizeOf(typeof(DEVMODE));
        if (EnumDisplaySettingsEx(dev, -1, ref dm, 0) == 0)
        {
            Say("EnumDisplaySettingsEx FAILED for " + dev);
            log.Close();
            return 1;
        }
        Say(string.Format("current = {0}x{1}@{2} fields=0x{3:x}", dm.dmPelsWidth, dm.dmPelsHeight,
                          dm.dmDisplayFrequency, dm.dmFields));
        dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY;

        Say("CDS_TEST            rc=" + ChangeDisplaySettingsEx(dev, ref dm, IntPtr.Zero, CDS_TEST, IntPtr.Zero));
        Say("CDS_UPDATEREGISTRY  rc=" + ChangeDisplaySettingsEx(dev, ref dm, IntPtr.Zero, CDS_UPDATEREGISTRY, IntPtr.Zero));
        Say("SDC_VALIDATE        rc=" + SetDisplayConfig(0, IntPtr.Zero, 0, IntPtr.Zero, SDC_TOPOLOGY_EXTEND | SDC_VALIDATE));
        Say("SDC_APPLY           rc=" + SetDisplayConfig(0, IntPtr.Zero, 0, IntPtr.Zero, SDC_TOPOLOGY_EXTEND | SDC_APPLY));
        Say("DONE");
        log.Close();
        return 0;
    }
}
