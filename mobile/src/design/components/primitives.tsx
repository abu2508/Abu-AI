import React from 'react';
import {
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text as RNText,
  View,
  type AccessibilityRole,
  type StyleProp,
  type TextProps,
  type ViewStyle,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { useTheme } from '../theme';
import { hairline, minTarget, radius, space, typeScale, type TextVariant } from '../tokens';
import type { Palette } from '../tokens';

export type TextTone = 'primary' | 'secondary' | 'tertiary' | 'brand' | 'onAccent' | 'danger' | 'success' | 'warning';

function toneColor(c: Palette, tone: TextTone): string {
  switch (tone) {
    case 'secondary':
      return c.textSecondary;
    case 'tertiary':
      return c.textTertiary;
    case 'brand':
      return c.brand;
    case 'onAccent':
      return c.textOnAccent;
    case 'danger':
      return c.danger;
    case 'success':
      return c.success;
    case 'warning':
      return c.warning;
    default:
      return c.text;
  }
}

/** Role-based text. Scales with the user's text size (Dynamic Type) by default; money uses tabular figures. */
export function Text({
  variant = 'body',
  tone = 'primary',
  numeric,
  style,
  ...rest
}: TextProps & { variant?: TextVariant; tone?: TextTone; numeric?: boolean }) {
  const { colors } = useTheme();
  const spec = typeScale[variant];
  const heading = variant === 'display' || variant.startsWith('title');
  return (
    <RNText
      accessibilityRole={heading ? 'header' : undefined}
      {...rest}
      style={[
        {
          color: toneColor(colors, tone),
          fontSize: spec.fontSize,
          lineHeight: spec.lineHeight,
          fontWeight: spec.fontWeight,
        },
        numeric ? { fontVariant: ['tabular-nums'] } : null,
        style,
      ]}
    />
  );
}

export function Screen({
  children,
  scroll = true,
  padded = true,
  bottomInset = 0,
  onRefresh,
  refreshing = false,
}: {
  children: React.ReactNode;
  scroll?: boolean;
  padded?: boolean;
  bottomInset?: number;
  /** Pull to refresh (only when provided). */
  onRefresh?: () => void;
  refreshing?: boolean;
}) {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const content = {
    paddingHorizontal: padded ? space.lg : 0,
    paddingTop: insets.top + space.sm,
    paddingBottom: space.xxxl + bottomInset,
    gap: space.lg,
  };
  if (!scroll) return <View style={[{ flex: 1, backgroundColor: colors.bg }, content]}>{children}</View>;
  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: colors.bg }}
      contentContainerStyle={content}
      contentInsetAdjustmentBehavior="never"
      keyboardShouldPersistTaps="handled"
      refreshControl={onRefresh ? <RefreshControl refreshing={refreshing} onRefresh={onRefresh} /> : undefined}
    >
      {children}
    </ScrollView>
  );
}

export function Row({
  children,
  style,
  gap = space.md,
}: {
  children: React.ReactNode;
  style?: StyleProp<ViewStyle>;
  gap?: number;
}) {
  return <View style={[{ flexDirection: 'row', alignItems: 'center', gap }, style]}>{children}</View>;
}

export function Card({
  children,
  accent,
  style,
}: {
  children: React.ReactNode;
  accent?: 'finance' | 'messaging' | 'coach' | 'brand';
  style?: StyleProp<ViewStyle>;
}) {
  const { colors } = useTheme();
  return (
    <View
      style={[
        {
          backgroundColor: colors.card,
          borderRadius: radius.lg,
          padding: space.lg,
          gap: space.md,
          borderWidth: hairline,
          borderColor: colors.separator,
          borderLeftWidth: accent ? 4 : hairline,
          borderLeftColor: accent ? colors[accent] : colors.separator,
        },
        style,
      ]}
    >
      {children}
    </View>
  );
}

export function Section({
  title,
  children,
  action,
}: {
  title: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <View style={{ gap: space.sm }}>
      <Row style={{ justifyContent: 'space-between' }}>
        <Text variant="title3">{title}</Text>
        {action}
      </Row>
      {children}
    </View>
  );
}

export function Divider() {
  const { colors } = useTheme();
  return (
    <View
      style={{ height: hairline, backgroundColor: colors.separator }}
      accessibilityElementsHidden
      importantForAccessibility="no"
    />
  );
}

type ButtonKind = 'primary' | 'secondary' | 'quiet' | 'destructive';

export function Button({
  label,
  onPress,
  kind = 'primary',
  disabled,
  accessibilityHint,
}: {
  label: string;
  onPress: () => void;
  kind?: ButtonKind;
  disabled?: boolean;
  accessibilityHint?: string;
}) {
  const { colors } = useTheme();
  const bg =
    kind === 'primary'
      ? colors.brand
      : kind === 'destructive'
        ? colors.dangerSoft
        : kind === 'secondary'
          ? colors.brandSoft
          : 'transparent';
  const fg = kind === 'primary' ? colors.textOnAccent : kind === 'destructive' ? colors.danger : colors.brand;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityHint={accessibilityHint}
      accessibilityState={{ disabled: !!disabled }}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => ({
        minHeight: minTarget,
        paddingHorizontal: space.xl,
        borderRadius: radius.pill,
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: bg,
        opacity: disabled ? 0.45 : pressed ? 0.85 : 1,
      })}
    >
      <RNText style={{ color: fg, fontSize: typeScale.headline.fontSize, fontWeight: '600' }}>{label}</RNText>
    </Pressable>
  );
}

export function Chip({
  label,
  selected,
  onPress,
  tone = 'brand',
}: {
  label: string;
  selected?: boolean;
  onPress?: () => void;
  tone?: 'brand' | 'finance' | 'messaging' | 'coach';
}) {
  const { colors } = useTheme();
  const soft = colors[`${tone}Soft` as keyof Palette];
  const strong = colors[tone];
  const content = (
    <View
      style={{
        minHeight: 32,
        paddingHorizontal: space.md,
        borderRadius: radius.pill,
        justifyContent: 'center',
        backgroundColor: selected ? strong : soft,
      }}
    >
      <RNText
        style={{
          color: selected ? colors.textOnAccent : strong,
          fontSize: typeScale.footnote.fontSize,
          fontWeight: '600',
        }}
      >
        {label}
      </RNText>
    </View>
  );
  if (!onPress) return content;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ selected: !!selected }}
      onPress={onPress}
      style={{ minHeight: minTarget, justifyContent: 'center' }}
    >
      {content}
    </Pressable>
  );
}

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
  label: string;
}) {
  const { colors } = useTheme();
  return (
    <View
      accessibilityRole={'radiogroup' as AccessibilityRole}
      accessibilityLabel={label}
      style={{ flexDirection: 'row', backgroundColor: colors.bgSunken, borderRadius: radius.md, padding: 3 }}
    >
      {options.map((o) => {
        const selected = o.value === value;
        return (
          <Pressable
            key={o.value}
            accessibilityRole="radio"
            accessibilityLabel={o.label}
            accessibilityState={{ selected, checked: selected }}
            onPress={() => onChange(o.value)}
            style={{
              flex: 1,
              minHeight: minTarget,
              alignItems: 'center',
              justifyContent: 'center',
              borderRadius: radius.sm + 1,
              backgroundColor: selected ? colors.card : 'transparent',
              borderWidth: selected ? hairline : 0,
              borderColor: colors.separator,
            }}
          >
            <RNText
              style={{
                color: selected ? colors.text : colors.textSecondary,
                fontWeight: selected ? '600' : '400',
                fontSize: typeScale.callout.fontSize,
              }}
            >
              {o.label}
            </RNText>
          </Pressable>
        );
      })}
    </View>
  );
}

export function Banner({
  tone = 'info',
  title,
  children,
}: {
  tone?: 'info' | 'warning' | 'danger' | 'success';
  title?: string;
  children: React.ReactNode;
}) {
  const { colors } = useTheme();
  const bg = colors[`${tone}Soft` as keyof Palette];
  const fg = colors[tone];
  return (
    <View
      accessibilityRole="alert"
      style={{
        backgroundColor: bg,
        borderRadius: radius.md,
        padding: space.md,
        gap: space.xs,
        borderWidth: hairline,
        borderColor: fg,
      }}
    >
      {title ? (
        <RNText style={{ color: fg, fontWeight: '700', fontSize: typeScale.callout.fontSize }}>{title}</RNText>
      ) : null}
      <RNText style={{ color: fg, fontSize: typeScale.footnote.fontSize, lineHeight: typeScale.footnote.lineHeight }}>
        {children}
      </RNText>
    </View>
  );
}

export function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <View style={{ alignItems: 'center', gap: space.sm, paddingVertical: space.xxxl }}>
      <Text variant="headline" style={{ textAlign: 'center' }}>
        {title}
      </Text>
      <Text variant="callout" tone="secondary" style={{ textAlign: 'center' }}>
        {body}
      </Text>
    </View>
  );
}

export function ListRow({
  title,
  subtitle,
  trailing,
  leading,
  onPress,
  accessibilityHint,
}: {
  title: string;
  subtitle?: string;
  trailing?: React.ReactNode;
  leading?: React.ReactNode;
  onPress?: () => void;
  accessibilityHint?: string;
}) {
  const { colors } = useTheme();
  const body = (
    <Row style={{ minHeight: minTarget, paddingVertical: space.xs }}>
      {leading}
      <View style={{ flex: 1 }}>
        <Text variant="body">{title}</Text>
        {subtitle ? (
          <Text variant="footnote" tone="secondary">
            {subtitle}
          </Text>
        ) : null}
      </View>
      {trailing}
    </Row>
  );
  if (!onPress) return body;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={subtitle ? `${title}, ${subtitle}` : title}
      accessibilityHint={accessibilityHint}
      onPress={onPress}
      style={({ pressed }) => ({
        minHeight: minTarget,
        justifyContent: 'center',
        backgroundColor: pressed ? colors.cardPressed : 'transparent',
        borderRadius: radius.sm,
      })}
    >
      {body}
    </Pressable>
  );
}

export const styles = StyleSheet.create({});
