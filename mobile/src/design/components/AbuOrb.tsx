import React, { useEffect, useState } from 'react';
import { Animated, Easing, Platform, View } from 'react-native';

import { BloomMark } from '../brand/BloomMark';
import { useReduceMotion } from '../a11y';
import { useTheme } from '../theme';
import { motion } from '../tokens';

export type OrbState = 'idle' | 'thinking' | 'done' | 'attention' | 'blocked';

const LABEL: Record<OrbState, string> = {
  idle: 'Abu is ready',
  thinking: 'Abu is working',
  done: 'Abu finished',
  attention: 'Abu needs your attention',
  blocked: 'Abu is stopped',
};

/**
 * The Abu presence. States: idle (slow breathing glow), thinking (a node orbits the Bloom), done (one settle pulse),
 * attention (a single bounce), blocked (dimmed, no motion). "thinking" reflects the REAL request lifecycle only:
 * it never invents progress steps. Reduce Motion replaces movement with a static glow / opacity change.
 * Built on the core Animated API (native driver off web); Reanimated/Skia arrive later in the approved register.
 */
export function AbuOrb({ state = 'idle', size = 56 }: { state?: OrbState; size?: number }) {
  const { colors } = useTheme();
  const reduce = useReduceMotion();
  const [breathe] = useState(() => new Animated.Value(0));
  const [orbit] = useState(() => new Animated.Value(0));
  const [pulse] = useState(() => new Animated.Value(0));
  const native = Platform.OS !== 'web';

  useEffect(() => {
    breathe.setValue(0);
    orbit.setValue(0);
    pulse.setValue(0);
    if (reduce || state === 'blocked') return;
    let anim: Animated.CompositeAnimation | undefined;
    if (state === 'idle') {
      anim = Animated.loop(
        Animated.sequence([
          Animated.timing(breathe, {
            toValue: 1,
            duration: motion.breathe / 2,
            easing: Easing.inOut(Easing.sin),
            useNativeDriver: native,
          }),
          Animated.timing(breathe, {
            toValue: 0,
            duration: motion.breathe / 2,
            easing: Easing.inOut(Easing.sin),
            useNativeDriver: native,
          }),
        ]),
      );
    } else if (state === 'thinking') {
      anim = Animated.loop(
        Animated.timing(orbit, { toValue: 1, duration: motion.orbit, easing: Easing.linear, useNativeDriver: native }),
      );
    } else {
      anim = Animated.sequence([
        Animated.timing(pulse, {
          toValue: 1,
          duration: motion.duration.base,
          easing: Easing.out(Easing.quad),
          useNativeDriver: native,
        }),
        Animated.spring(pulse, { toValue: 0, useNativeDriver: native, friction: 6 }),
      ]);
    }
    anim.start();
    return () => anim?.stop();
  }, [state, reduce, breathe, orbit, pulse, native]);

  const glowScale = breathe.interpolate({ inputRange: [0, 1], outputRange: [1, 1.18] });
  const glowOpacity =
    state === 'thinking' ? 0.55 : breathe.interpolate({ inputRange: [0, 1], outputRange: [0.28, 0.5] });
  const spin = orbit.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '360deg'] });
  const bump = pulse.interpolate({ inputRange: [0, 1], outputRange: [1, 1.12] });
  const dim = state === 'blocked';
  const reducedThinking = reduce && state === 'thinking';

  return (
    <View
      accessible
      accessibilityRole="image"
      accessibilityLabel={LABEL[state]}
      accessibilityLiveRegion="polite"
      style={{
        width: size,
        height: size,
        alignItems: 'center',
        justifyContent: 'center',
        opacity: dim ? 0.4 : reducedThinking ? 0.8 : 1,
      }}
    >
      <Animated.View
        style={{
          position: 'absolute',
          width: size,
          height: size,
          borderRadius: size / 2,
          backgroundColor: state === 'attention' ? colors.coach : colors.bloomB,
          opacity: dim ? 0.1 : reduce ? 0.32 : glowOpacity,
          transform: [{ scale: reduce ? 1 : glowScale }],
        }}
      />
      <Animated.View style={{ transform: [{ scale: reduce ? 1 : bump }] }}>
        <BloomMark
          size={size * 0.8}
          from={colors.bloomA}
          to={colors.bloomB}
          node={dim ? colors.textTertiary : '#FFFFFF'}
        />
      </Animated.View>
      {state === 'thinking' && !reduce ? (
        <Animated.View style={{ position: 'absolute', width: size, height: size, transform: [{ rotate: spin }] }}>
          <View
            style={{
              position: 'absolute',
              top: 1,
              left: size / 2 - 3,
              width: 6,
              height: 6,
              borderRadius: 3,
              backgroundColor: colors.bloomA,
            }}
          />
        </Animated.View>
      ) : null}
    </View>
  );
}
