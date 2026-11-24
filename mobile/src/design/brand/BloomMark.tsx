import React from 'react';
import Svg, { Circle, Defs, Ellipse, G, LinearGradient, Stop } from 'react-native-svg';

/**
 * The Abu "Bloom": two overlapping translucent lobes that read as a soft "A", with one bright node at the apex.
 * Original artwork (no HUD rings, no arc-reactor shapes, no Apple glyphs). Geometry mirrors assets/brand/bloom.svg.
 */
export function BloomMark({
  size = 64,
  from = '#5B6CFF',
  to = '#8A5CFF',
  node = '#FFFFFF',
  title,
}: {
  size?: number;
  from?: string;
  to?: string;
  node?: string;
  title?: string;
}) {
  return (
    <Svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      accessibilityRole={title ? 'image' : undefined}
      accessibilityLabel={title}
      accessible={!!title}
      importantForAccessibility={title ? 'yes' : 'no-hide-descendants'}
    >
      <Defs>
        <LinearGradient id="bloomL" x1="0" y1="0" x2="1" y2="1">
          <Stop offset="0" stopColor={from} stopOpacity={0.95} />
          <Stop offset="1" stopColor={to} stopOpacity={0.85} />
        </LinearGradient>
        <LinearGradient id="bloomR" x1="1" y1="0" x2="0" y2="1">
          <Stop offset="0" stopColor={to} stopOpacity={0.95} />
          <Stop offset="1" stopColor={from} stopOpacity={0.85} />
        </LinearGradient>
      </Defs>
      <G rotation={20} originX={38} originY={60}>
        <Ellipse cx={38} cy={60} rx={16} ry={34} fill="url(#bloomL)" />
      </G>
      <G rotation={-20} originX={62} originY={60}>
        <Ellipse cx={62} cy={60} rx={16} ry={34} fill="url(#bloomR)" />
      </G>
      <Circle cx={50} cy={20} r={5.5} fill={node} />
    </Svg>
  );
}
