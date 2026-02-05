/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./static/js/**/*.js",
  ],
  safelist: [
    // Sidebar responsive classes (used by Alpine.js)
    "w-0",
    "w-16",
    "w-64",
    "ml-0",
    "ml-16",
    "ml-64",
    "md:pl-16",
    "md:pl-64",
    "md:ml-16",
    "md:ml-20",
    "md:ml-64",
    "md:hidden",
    "lg:hidden",
    "translate-x-0",
    "-translate-x-full",
    "md:translate-x-0",
    // Grid responsive patterns
    "sm:grid-cols-2",
    "md:grid-cols-3",
    "lg:grid-cols-4",
    "xl:grid-cols-5",
    // Flex responsive patterns
    "sm:flex-row",
    "md:flex-row",
    "sm:items-center",
    "md:items-center",
    // Display responsive patterns
    "sm:block",
    "md:block",
    "lg:block",
    "sm:hidden",
    "sm:inline",
    "sm:inline-flex",
    "md:inline-flex",
    // Module color variants (used in topbar via Jinja2 dict lookups)
    "bg-teal-50/50", "dark:bg-teal-900/10", "bg-teal-500",
    "bg-violet-50/50", "dark:bg-violet-900/10", "bg-violet-500",
    "bg-amber-50/50", "dark:bg-amber-900/10", "bg-amber-500",
    // Sidebar active link colors
    "bg-teal-50", "dark:bg-teal-900/20", "text-teal-800", "dark:text-teal-200",
    "bg-amber-50", "dark:bg-amber-900/20", "text-amber-800", "dark:text-amber-200",
    "bg-violet-50", "dark:bg-violet-900/20", "text-violet-800", "dark:text-violet-200",
    // Width patterns for tables
    "sm:w-auto",
    "md:w-auto",
    // Spacing responsive patterns
    "sm:gap-4",
    "md:gap-6",
    "lg:gap-8",
    "sm:p-6",
    "md:p-8",
    "lg:p-10",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        sans: ['DM Sans', 'system-ui', 'sans-serif'],
        display: ['Fraunces', 'Georgia', 'serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'monospace'],
      },
      colors: {
        // Refined teal-cyan palette
        primary: {
          50: '#f0fdfa',
          100: '#ccfbf1',
          200: '#99f6e4',
          300: '#5eead4',
          400: '#2dd4bf',
          500: '#14b8a6',
          600: '#0d9488',
          700: '#0f766e',
          800: '#115e59',
          900: '#134e4a',
          950: '#042f2e',
        },
        // Warm gold accent
        accent: {
          50: '#fffbeb',
          100: '#fef3c7',
          200: '#fde68a',
          300: '#fcd34d',
          400: '#fbbf24',
          500: '#f59e0b',
          600: '#d97706',
          700: '#b45309',
          800: '#92400e',
          900: '#78350f',
          950: '#451a03',
        },
        // Deep navy ink colors for text - signals trust and professionalism
        ink: {
          DEFAULT: '#0f172a',
          light: '#334155',
          muted: '#64748b',
          faint: '#94a3b8',
        },
        // Module colors for easy Tailwind access
        'module-finance': '#0d9488',
        'module-people': '#8b5cf6',
        'module-expense': '#f59e0b',
        'module-operations': '#3b82f6',
        'module-admin': '#6366f1',
      },
      borderRadius: {
        'card': '16px',
        'btn': '10px',
        'input': '10px',
        'badge': '6px',
        'icon': '12px',
      },
      spacing: {
        'input-x': '16px',
        'input-y': '12px',
        'card': '24px',
        'card-sm': '16px',
        'card-lg': '32px',
      },
      boxShadow: {
        'card': '0 1px 3px rgba(15, 23, 42, 0.04), 0 4px 12px rgba(15, 23, 42, 0.06)',
        'card-hover': '0 8px 24px rgba(15, 23, 42, 0.12)',
        'btn': '0 4px 12px rgba(26, 31, 54, 0.25)',
        'btn-hover': '0 8px 20px rgba(26, 31, 54, 0.35)',
      },
      // Animations defined in input.css to avoid duplication
      // Use @keyframes in CSS for complex animations
    }
  },
  plugins: [],
}
