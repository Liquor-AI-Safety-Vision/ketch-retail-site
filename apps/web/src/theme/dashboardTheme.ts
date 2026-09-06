import { alpha, createTheme } from '@mui/material/styles';
import type { ColorMode } from './ColorModeProvider';

export function createDashboardTheme(mode: ColorMode) {
  const dark = mode === 'dark';
  const canvas = dark ? '#0B100E' : '#F3F5F2';
  const paper = dark ? '#111815' : '#FCFDFB';
  const divider = dark ? '#29332E' : '#D8DED9';
  const primary = dark ? '#62B68B' : '#246A50';
  const elevation = dark
    ? '0 1px 2px rgba(0, 0, 0, 0.34), 0 16px 40px -26px rgba(0, 0, 0, 0.72)'
    : 'none';

  return createTheme({
    palette: {
      mode,
      primary: {
        main: primary,
        light: dark ? '#8DCDAA' : '#4C8A70',
        dark: dark ? '#3F8E68' : '#164C38',
        contrastText: '#FFFFFF',
      },
      secondary: {
        main: dark ? '#A8B5AE' : '#5F6E66',
        light: dark ? '#CBD4CF' : '#839087',
        dark: dark ? '#77867E' : '#435149',
      },
      background: { default: canvas, paper },
      text: {
        primary: dark ? '#F0F4F1' : '#18201C',
        secondary: dark ? '#9AA69F' : '#657069',
      },
      divider,
      success: { main: dark ? '#74C495' : '#327A51' },
      warning: { main: dark ? '#D5A85F' : '#99601F' },
      error: { main: dark ? '#E87972' : '#B63E3A' },
      info: { main: dark ? '#76AABD' : '#39768A' },
      action: {
        hover: alpha(primary, dark ? 0.09 : 0.055),
        selected: alpha(primary, dark ? 0.14 : 0.1),
      },
    },
    shape: { borderRadius: 6 },
    typography: {
      fontFamily: '"Aptos", "Segoe UI Variable", "Segoe UI", sans-serif',
      h4: { fontWeight: 650, letterSpacing: '-0.04em' },
      h5: { fontWeight: 650, letterSpacing: '-0.035em' },
      h6: { fontWeight: 650, letterSpacing: '-0.02em' },
      button: { letterSpacing: '-0.01em' },
    },
    components: {
      MuiCssBaseline: {
        styleOverrides: {
          body: {
            backgroundColor: canvas,
            color: dark ? '#F0F4F1' : '#18201C',
            scrollbarColor: dark ? '#35433B #0B100E' : '#B9C1BB #F3F5F2',
          },
          '::selection': { backgroundColor: primary, color: '#FFFFFF' },
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
          paper: { backgroundColor: paper, color: dark ? '#F0F4F1' : '#18201C' },
        },
      },
    },
  });
}
