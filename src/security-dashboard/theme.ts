import { createTheme } from '@mui/material/styles';
import type { ColorMode } from '../theme/ColorModeProvider';

export function createDashboardTheme(mode: ColorMode) {
  const dark = mode === 'dark';
  const canvas = dark ? '#0C0E0F' : '#F5F2EB';
  const paper = dark ? '#14191A' : '#FFFCF7';
  const divider = dark ? '#282E2D' : '#D5D0C6';
  const primary = dark ? '#D8A35D' : '#9A6128';
  /* Dark surfaces need a soft drop shadow on top of hairline borders,
     otherwise panels at different elevations read as one flat plane. */
  const elevation = dark
    ? '0 1px 2px rgba(0, 0, 0, 0.40), 0 12px 32px -16px rgba(0, 0, 0, 0.60)'
    : 'none';

  return createTheme({
    palette: {
      mode,
      primary: {
        main: primary,
        light: dark ? '#F0C987' : '#BF8242',
        dark: dark ? '#A86F2F' : '#754617',
        contrastText: dark ? '#130F0A' : '#FFFFFF',
      },
      secondary: {
        main: dark ? '#91B3A2' : '#4E7D69',
        light: dark ? '#B8D1C4' : '#719B88',
        dark: dark ? '#5F8975' : '#365D4C',
      },
      background: { default: canvas, paper },
      text: {
        primary: dark ? '#F4EFE6' : '#202421',
        secondary: dark ? '#989C99' : '#676D69',
      },
      divider,
      success: { main: dark ? '#78B990' : '#397B52' },
      warning: { main: primary },
      error: { main: dark ? '#E06B65' : '#B6433E' },
      info: { main: dark ? '#72A7B5' : '#3F788A' },
    },
    shape: { borderRadius: 6 },
    typography: {
      fontFamily: '"Outfit Variable", Outfit, "Aptos", "Segoe UI", sans-serif',
      h4: { fontWeight: 520, letterSpacing: '-0.045em' },
      h5: { fontWeight: 560, letterSpacing: '-0.035em' },
      h6: { fontWeight: 600, letterSpacing: '-0.02em' },
      button: { letterSpacing: '-0.01em' },
    },
    components: {
      MuiCssBaseline: {
        styleOverrides: {
          body: {
            backgroundColor: canvas,
            color: dark ? '#F4EFE6' : '#202421',
            backgroundImage: dark
              ? 'radial-gradient(circle at 82% -10%, rgba(216, 163, 93, 0.09), transparent 32%)'
              : 'radial-gradient(circle at 82% -10%, rgba(154, 97, 40, 0.08), transparent 31%)',
            scrollbarColor: dark ? '#3A413F #0C0E0F' : '#B9B3A8 #F5F2EB',
          },
          '::selection': { backgroundColor: primary, color: dark ? '#130F0A' : '#FFFFFF' },
        },
      },
      MuiPaper: {
        styleOverrides: {
          root: { backgroundImage: 'none', backgroundColor: paper, borderColor: divider },
          outlined: { boxShadow: elevation },
        },
      },
      MuiCard: {
        styleOverrides: {
          root: {
            backgroundImage: 'none',
            backgroundColor: paper,
            border: `1px solid ${divider}`,
            borderRadius: 6,
            boxShadow: elevation,
            transition: 'transform 260ms ease, border-color 260ms ease, box-shadow 260ms ease',
          },
        },
      },
      MuiButton: {
        styleOverrides: {
          root: { borderRadius: 4, textTransform: 'none', fontWeight: 650, minHeight: 40 },
        },
      },
      MuiChip: {
        styleOverrides: { root: { borderRadius: 3, fontWeight: 650 } },
      },
      MuiDialog: {
        styleOverrides: {
          paper: { backgroundColor: paper, color: dark ? '#F4EFE6' : '#202421' },
        },
      },
    },
  });
}
