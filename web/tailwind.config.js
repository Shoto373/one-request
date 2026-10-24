/** @type {import('tailwindcss').Config} */
// Токены сняты с ostrovok.ru (CSS-переменные --mpf-*), шрифт — Onest как открытая замена PT Root UI.
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Onest Variable"', 'Helvetica', 'Arial', 'sans-serif'],
        // широкий гротеск заголовков ostrovok.ru — открытая замена Unbounded
        display: ['"Unbounded Variable"', '"Onest Variable"', 'Helvetica', 'sans-serif'],
      },
      colors: {
        brand: {
          DEFAULT: '#0d41d2', // accent-blue-deep
          bright: '#0a4fec', // accent-blue
          hover: '#184cdc',
          light: '#eaf2ff', // bg-light-blue
          soft: '#dfe7ff',
        },
        lime: { DEFAULT: '#acef81', soft: '#e3fad5', deep: '#53af24' },
        guru: { from: '#4225f2', to: '#0460ea' }, // градиент AI-ассистента
        ink: { DEFAULT: '#2d3137', grey: '#464a4f', muted: '#868686', line: '#e5e5e5' },
        canvas: '#f4f4f4',
        ok: { DEFAULT: '#53af24', bg: '#e5f3de' },
        warn: { DEFAULT: '#e77d00', bg: '#fff8e6' },
        bad: { DEFAULT: '#ce2121', bg: '#fae9e9' },
      },
      borderRadius: { xl: '12px', '2xl': '16px', '3xl': '24px' },
      boxShadow: {
        card: '0 1px 2px rgba(0,0,0,.04), 0 4px 16px rgba(0,0,0,.04)',
        lift: '0 8px 28px rgba(13,65,210,.12)',
      },
      maxWidth: { page: '1380px' },
    },
  },
  plugins: [],
}
