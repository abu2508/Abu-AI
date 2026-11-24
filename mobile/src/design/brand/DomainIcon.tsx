import React from 'react';
import Svg, { Circle, Path } from 'react-native-svg';

export type Domain = 'finance' | 'messaging' | 'coach';

/** Original domain glyphs (24-pt grid, 1.75 stroke): coin-with-trend, speech lobe, sunrise arc. */
export function DomainIcon({ domain, size = 24, color }: { domain: Domain; size?: number; color: string }) {
  const common = {
    stroke: color,
    strokeWidth: 1.75,
    strokeLinecap: 'round',
    strokeLinejoin: 'round',
    fill: 'none',
  } as const;
  return (
    <Svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      accessible={false}
      importantForAccessibility="no-hide-descendants"
    >
      {domain === 'finance' ? (
        <>
          <Circle cx={12} cy={12} r={9} {...common} />
          <Path d="M7.5 14.5 L10.5 11 L13 13.2 L16.5 8.8" {...common} />
        </>
      ) : null}
      {domain === 'messaging' ? (
        <>
          <Path d="M5 6.2Q5 4 7.2 4H16.8Q19 4 19 6.2V12.8Q19 15 16.8 15H12L8 19V15H7.2Q5 15 5 12.8Z" {...common} />
          <Path d="M9 9.5H15" {...common} />
        </>
      ) : null}
      {domain === 'coach' ? (
        <>
          <Path d="M3 18H21" {...common} />
          <Path d="M7 18A5 5 0 0 1 17 18" {...common} />
          <Path d="M12 6V8.2M5.6 9.6L7.1 11.1M18.4 9.6L16.9 11.1" {...common} />
        </>
      ) : null}
    </Svg>
  );
}
