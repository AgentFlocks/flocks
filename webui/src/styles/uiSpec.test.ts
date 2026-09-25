import { readFileSync } from 'node:fs'
import path from 'node:path'

import postcss from 'postcss'
import tailwindcss from 'tailwindcss'
import { beforeAll, describe, expect, it } from 'vitest'

import tailwindConfig from '../../tailwind.config.js'

const tokens = readFileSync(path.join(__dirname, 'tokens.css'), 'utf8')

// Exercise Tailwind's emitted CSS, not a second implementation of its config.
// The authoritative light/dark values are separately compared with the design
// HTML by scripts/ui_spec_check.py; this protects the utility-to-token wiring.
const colorUtilities: Record<string, [string, string]> = {
  'bg-canvas': ['background-color', '--bg-canvas'],
  'bg-surface': ['background-color', '--bg-surface'],
  'bg-subtle': ['background-color', '--bg-subtle'],
  'bg-hover': ['background-color', '--bg-hover'],
  'bg-selected': ['background-color', '--bg-selected'],
  'text-fg': ['color', '--text-primary'],
  'text-fg-secondary': ['color', '--text-secondary'],
  'text-fg-muted': ['color', '--text-muted'],
  'text-fg-placeholder': ['color', '--text-placeholder'],
  'text-fg-link': ['color', '--text-link'],
  'text-fg-disabled': ['color', '--disabled-text'],
  'border-line': ['border-color', '--border-subtle'],
  'border-line-control': ['border-color', '--border-control'],
  'bg-action': ['background-color', '--action-primary'],
  'bg-action-hover': ['background-color', '--action-hover'],
  'bg-action-pressed': ['background-color', '--action-pressed'],
  'text-action-fg': ['color', '--action-onPrimary'],
  'bg-action-danger': ['background-color', '--action-danger'],
  'text-action-danger-fg': ['color', '--action-onDanger'],
  'outline-focus': ['outline-color', '--focus-ring'],
  'bg-disabled': ['background-color', '--disabled-bg'],
  'bg-switch-on': ['background-color', '--switch-on'],
  'bg-switch-thumb': ['background-color', '--switch-thumb'],
  'bg-overlay-backdrop': ['background-color', '--overlay-backdrop'],
}
for (const state of ['success', 'warning', 'danger', 'info']) {
  colorUtilities[`text-${state}`] = ['color', `--status-${state}`]
  colorUtilities[`bg-${state}-soft`] = ['background-color', `--status-${state}Bg`]
}
for (const category of ['ai', 'data', 'brand']) {
  colorUtilities[`text-${category}`] = ['color', `--accent-${category}`]
  colorUtilities[`bg-${category}-soft`] = ['background-color', `--accent-${category}Bg`]
}

const geometryUtilities: Record<string, [string, string]> = {
  'rounded-tag': ['border-radius', '--radius-sm'],
  'rounded-segment': ['border-radius', '--radius-segment'],
  'rounded-control': ['border-radius', '--radius-control'],
  'rounded-card': ['border-radius', '--radius-card'],
  'rounded-overlay': ['border-radius', '--radius-overlay'],
  'rounded-pill': ['border-radius', '--radius-pill'],
  'h-control-sm': ['height', '--control-sm'],
  'h-control': ['height', '--control-md'],
  'h-control-lg': ['height', '--control-lg'],
  'min-h-touch': ['min-height', '--control-touch'],
  'w-sidebar': ['width', '--layout-sidebar'],
  'h-topbar': ['height', '--layout-topbar'],
  'max-w-modal': ['max-width', '--overlay-modal'],
  'max-w-drawer': ['max-width', '--overlay-drawer'],
  'max-w-onboarding': ['max-width', '--overlay-onboarding'],
  'shadow-overlay': ['--tw-shadow', '--shadow-overlay'],
  'duration-fast': ['transition-duration', '--motion-fast'],
  'duration-overlay': ['transition-duration', '--motion-overlay'],
}
const typographyUtilities: Record<string, [string, string, string?]> = {
  'text-xs': ['12px', '18px'],
  'text-aux': ['12px', '18px'],
  'text-sm': ['14px', '22px'],
  'text-body': ['14px', '22px'],
  'text-title-card': ['16px', '24px', '600'],
  'text-title-section': ['20px', '28px', '600'],
  'text-title-page': ['24px', '32px', '700'],
  'text-3xl': ['28px', '36px'],
}

const declarations = new Map<string, Map<string, string>>()
beforeAll(async () => {
  const classes = [
    ...Object.keys(colorUtilities),
    ...Object.keys(geometryUtilities),
    ...Object.keys(typographyUtilities),
    'font-sans', 'font-mono', 'transition-colors',
  ]
  const result = await postcss([
    tailwindcss({
      ...tailwindConfig,
      content: [{ raw: classes.join(' '), extension: 'html' }],
    }),
  ]).process('@tailwind utilities;', { from: undefined })
  result.root.walkRules((rule) => {
    const properties = declarations.get(rule.selector) ?? new Map<string, string>()
    rule.walkDecls((declaration) => { properties.set(declaration.prop, declaration.value) })
    declarations.set(rule.selector, properties)
  })
})

function property(utility: string, name: string) {
  return declarations.get(`.${utility}`)?.get(name)
}

function lightToken(name: string) {
  let value: string | undefined
  postcss.parse(tokens).walkRules(':root', (rule) => {
    rule.walkDecls(name, (declaration) => { value = declaration.value })
  })
  return value
}

describe('Flocks UI specification utility contract', () => {
  it('emits every semantic colour as its theme-aware token', () => {
    for (const [utility, [name, token]] of Object.entries(colorUtilities)) {
      expect(property(utility, name), utility).toBe(`var(${token})`)
    }
  })

  it('preserves control, overlay, layout and motion token mappings', () => {
    for (const [utility, [name, token]] of Object.entries(geometryUtilities)) {
      expect(property(utility, name), utility).toBe(`var(${token})`)
    }
    expect(property('transition-colors', 'transition-duration')).toBe('var(--motion-normal)')
    expect(property('transition-colors', 'transition-timing-function')).toBe('var(--motion-easing)')
  })

  it('preserves component dimensions that the design specifies outside its token table', () => {
    // The HTML examples and UI_SPEC.md specify these dimensions directly;
    // implementation tokens must not drift just because the HTML has no alias.
    expect(lightToken('--radius-segment')).toBe('6px')
    expect(lightToken('--overlay-modal')).toBe('560px')
    expect(lightToken('--overlay-drawer')).toBe('480px')
    expect(lightToken('--overlay-onboarding')).toBe('768px')
  })

  it('keeps the 14/22 body, 12/18 auxiliary and heading/metric scales', () => {
    for (const [utility, [size, lineHeight, weight]] of Object.entries(typographyUtilities)) {
      expect(property(utility, 'font-size'), utility).toBe(size)
      expect(property(utility, 'line-height'), utility).toBe(lineHeight)
      if (weight) expect(property(utility, 'font-weight'), utility).toBe(weight)
    }
  })

  it('uses the same font stacks for CSS baseline and utilities', () => {
    const normalize = (value: string) => value.replace(/["']/g, '').replace(/\s*,\s*/g, ',').trim()
    for (const family of ['sans', 'mono']) {
      const baseline = lightToken(`--font-${family}`)
      const utility = property(`font-${family}`, 'font-family')
      expect(baseline, `--font-${family}`).toBeTruthy()
      expect(utility, `font-${family}`).toBeTruthy()
      expect(normalize(utility!)).toBe(normalize(baseline!))
    }
  })
})
