import React from 'react';
import { Platform, View } from 'react-native';
import { SymbolView, type SFSymbol } from 'expo-symbols';

import { Text } from './primitives';

/**
 * Generic system glyphs use Apple SF Symbols on iOS (as licensed for apps; never copied as assets). Brand and
 * domain glyphs are our own (see design/brand). On web/Android, where SF Symbols do not exist, a neutral text
 * stand-in is shown so the gallery and tests still render.
 */
export function Icon({
  name,
  size = 22,
  color,
  fallback,
}: {
  name: SFSymbol;
  size?: number;
  color: string;
  fallback: string;
}) {
  if (Platform.OS === 'ios') {
    return <SymbolView name={name} size={size} tintColor={color} accessible={false} />;
  }
  return (
    <View
      accessible={false}
      importantForAccessibility="no-hide-descendants"
      style={{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }}
    >
      <Text variant="callout" style={{ color, fontSize: size * 0.8, lineHeight: size }}>
        {fallback}
      </Text>
    </View>
  );
}
