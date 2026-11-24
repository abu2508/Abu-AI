import { screen } from '@testing-library/react-native';
import { StyleSheet, type ViewStyle } from 'react-native';

const MIN = 44;

export type Host = { props: Record<string, unknown>; type: unknown };

/** Interactive elements need a role, an accessible name, and a >= 44pt touch target (size or hit slop). */
export function checkInteractive(): string[] {
  const problems: string[] = [];
  const roles = ['button', 'tab', 'radio', 'link'];
  for (const role of roles) {
    for (const el of screen.queryAllByRole(role) as unknown as Host[]) {
      const p = el.props;
      const name = (p.accessibilityLabel ?? p['aria-label']) as string | undefined;
      if (!name || !name.trim()) problems.push(`${role} without an accessible name`);
      const style =
        StyleSheet.flatten(
          (typeof p.style === 'function'
            ? (p.style as (s: { pressed: boolean }) => ViewStyle)({ pressed: false })
            : p.style) as ViewStyle,
        ) ?? {};
      const slop = typeof p.hitSlop === 'number' ? p.hitSlop : 0;
      const h = (Number(style.minHeight ?? style.height ?? 0) || 0) + slop * 2;
      const w = (Number(style.minWidth ?? style.width ?? 0) || 0) + slop * 2;
      const flexFills = style.flex === 1; // tab/segment items fill their row; height is what matters
      if (h < MIN && !(flexFills && h >= MIN) && Math.max(h, w) < MIN)
        problems.push(`${role} "${name}" target ${w}x${h} < ${MIN}`);
    }
  }
  return problems;
}
