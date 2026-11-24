import React from 'react';
import { TextInput, View, type TextInputProps } from 'react-native';

import { useTheme } from '../theme';
import { hairline, minTarget, radius, space, typeScale } from '../tokens';
import { Text } from './primitives';

/** Labelled text input: >= 44 pt tall, themed, scales with Dynamic Type. */
export function TextField({ label, hint, style, ...rest }: TextInputProps & { label: string; hint?: string }) {
  const { colors } = useTheme();
  return (
    <View style={{ gap: space.xs }}>
      <Text variant="footnote" tone="secondary">
        {label}
      </Text>
      <TextInput
        accessibilityLabel={label}
        accessibilityHint={hint}
        placeholderTextColor={colors.textTertiary}
        autoCapitalize="none"
        autoCorrect={false}
        spellCheck={false}
        {...rest}
        style={[
          {
            minHeight: minTarget,
            borderRadius: radius.md,
            borderWidth: hairline,
            borderColor: colors.outline,
            backgroundColor: colors.card,
            color: colors.text,
            paddingHorizontal: space.md,
            fontSize: typeScale.body.fontSize,
          },
          style,
        ]}
      />
    </View>
  );
}
