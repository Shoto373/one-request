/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: { brand: { DEFAULT: '#0b6bcb', dark: '#0957a6', light: '#e7f1fb' } },
    },
  },
  plugins: [],
}
