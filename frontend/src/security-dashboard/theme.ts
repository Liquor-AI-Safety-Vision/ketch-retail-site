import { alpha, createTheme } from '@mui/material/styles';
import type { ColorMode } from '../theme/ColorModeProvider';

// Touch screens report a coarse pointer; narrow layouts are treated as touch too.
const coarse = '@media (pointer: coarse), (max-width: 899.95px)';

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
        secondary: dark ? '#A3AEA8' : '#58635D',
        disabled: dark ? '#7C8882' : '#7A847E',
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
    // One type system for the whole product (the landing page uses Outfit too).
    // Sizes: 12 / 14 / 16 / 20 / 28 / 34, plus display numbers on Overview.
    // Weights: 400 / 600 / 700. Uppercase labels: 0.08em tracking.
    typography: {
      fontFamily: '"Outfit Variable", "Outfit", system-ui, -apple-system, "Segoe UI", sans-serif',
      fontWeightLight: 400,
      fontWeightRegular: 400,
      fontWeightMedium: 600,
      fontWeightBold: 700,
      h4: { fontSize: '1.75rem', fontWeight: 600, lineHeight: 1.2, letterSpacing: '-0.02em', '@media (min-width:900px)': { fontSize: '2.125rem' } },
      h5: { fontSize: '1.25rem', fontWeight: 600, lineHeight: 1.3, letterSpacing: '-0.01em' },
      h6: { fontSize: '1rem', fontWeight: 600, lineHeight: 1.4 },
      subtitle1: { fontSize: '1rem', fontWeight: 600 },
      subtitle2: { fontSize: '0.875rem', fontWeight: 600 },
      body1: { fontSize: '1rem', lineHeight: 1.55 },
      body2: { fontSize: '0.875rem', lineHeight: 1.5 },
      caption: { fontSize: '0.75rem', lineHeight: 1.45 },
      overline: { fontSize: '0.75rem', fontWeight: 600, letterSpacing: '0.08em', lineHeight: 1.5 },
      button: { fontSize: '0.875rem', fontWeight: 600, letterSpacing: 0 },
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
      // Touch screens get 44 px targets (WCAG 2.5.5 / Apple HIG); a mouse keeps the denser sizes.
      MuiButton: {
        styleOverrides: {
          root: { borderRadius: 4, textTransform: 'none', fontWeight: 600, minHeight: 40, [coarse]: { minHeight: 44 } },
          sizeLarge: { fontSize: '1rem' },
        },
      },
      MuiIconButton: {
        styleOverrides: { root: { [coarse]: { minWidth: 44, minHeight: 44 } } },
      },
      MuiToggleButton: {
        styleOverrides: { root: { minHeight: 40, fontSize: '0.875rem', fontWeight: 600, textTransform: 'none', [coarse]: { minHeight: 44, minWidth: 44 } } },
      },
      MuiInputBase: {
        styleOverrides: { root: { [coarse]: { minHeight: 44 } } },
      },
      MuiPaginationItem: {
        styleOverrides: { root: { [coarse]: { minWidth: 44, height: 44 } } },
      },
      MuiChip: {
        styleOverrides: { root: { borderRadius: 3, fontWeight: 600, fontSize: '0.875rem' }, sizeSmall: { fontSize: '0.75rem' } },
      },
      MuiDialog: {
        styleOverrides: {
          paper: { backgroundColor: paper, color: dark ? '#F0F4F1' : '#18201C' },
        },
      },
    },
  });
}
