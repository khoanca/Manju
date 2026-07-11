# Test: Mobile / React Native

## PYRAMID
70-80% unit+component (Jest+RNTL) · 15-20% integration · 5-10% E2E (Detox/Maestro)

- RNTL: test via a11y labels+testIDs, not internals
- `jest.useFakeTimers()` for animations/async
- Mock `react-native-gesture-handler` + `reanimated` in setup
- Co-locate `.test.tsx` by feature
- Clear AsyncStorage/mocks/nav/Redux in beforeEach/afterEach

## CROSS-PLATFORM
- Single test suite both platforms; platform branches only when behavior genuinely differs
- CI: iOS simulators AND Android emulators; real devices for release
- Top 10-15 device/OS combos by analytics + cloud farms for breadth
- Emulators for dev(fast), real devices for pre-release(accurate)
- Platform UI: nav patterns, status bar, keyboard, permission dialogs

## DEVICE-SPECIFIC
- Constrained: 2GB RAM, slow CPU, 4-inch, tablets
- Density: ldpi→xxxhdpi (Android), @1x→@3x (iOS)
- A11y enabled: VoiceOver/TalkBack, large text, high contrast, reduced motion
- Interruptions: calls, alarms, low battery, USB, headphones
- Locales: RTL, long translations, date/number/currency formats

## PUSH NOTIFICATIONS
- All states: foreground/background/killed/lock screen
- Payload: title/body/image/deep link/custom data
- Permission: first-time/denied/re-request from settings
- Grouping/badges/sounds both platforms
- FCM+APNS in staging (not just local notifs)
- Tap→correct screen+data regardless of app state

## DEEP LINKING
- All entry points: push/SMS/email/QR/social/browser
- States: logged out(→login→target)/backgrounded/killed/fresh install(deferred)
- Universal Links(iOS)/App Links(Android) AND custom URI schemes
- Params: malformed/missing/expired/access-restricted
- Automate: `adb shell am start`(Android), `xcrun simctl openurl`(iOS)
- Same link works across push/in-app/browser

## PERFORMANCE
| Metric | Target |
|---|---|
| Cold start | <3s P95 |
| Screen render | <500ms P95 |
| Frame rate | >45fps avg (60 target) |

- Measure: cold start, nav render, re-render
- Profile: Xcode Instruments(iOS), Android Studio Profiler, React DevTools via Flipper
- JS thread stalls + dropped frames (primary jank causes)
- Perf regression in CI: compare baselines, fail on exceed
- Firebase Performance Monitoring for real-user metrics

## OFFLINE
- Full cycle: offline→actions→queue→online→sync→verify consistency
- Conflict: same data modified on 2 devices / offline+server
- Progressive degradation: works offline / cached+stale indicator / unavailable+clear msg
- Conditions: not just on/off but 2G/3G/intermittent/high latency
- Storage limits: cache full→graceful handling
- Jest+mocked network for unit; Detox/Maestro airplane mode for E2E

## GESTURES & ANIMATIONS
- Maestro/Detox: tap/long-press/swipe/scroll/pinch/multi-touch
- Wait for animation complete (`waitFor`), never `sleep()`
- Mock animations: `jest.mock('react-native-reanimated')` + fake timers
- Edge: rapid repeated, during transitions, near edges, multi-finger
- Real devices for final (emulator≠touch pressure/velocity/HW accel)
- Reduced motion: respect `accessibilityReduceMotionEnabled`

## APP STORE CHECKLIST
**Apple:** Crash test min iOS version (25% rejection rate) · latest iOS · privacy policy+nutrition labels · IAP: purchase/restore/subscription/receipt/sandbox · test credentials in App Store Connect · screenshots match app

**Google:** Target API 35 (Android 15) · Data Safety section matches behavior · min+latest OS

**Both:** Real conditions (calls/notifs/network change/low battery/storage) · a11y (readers/contrast/labels) · no debug builds, test creds stripped

## E2E FRAMEWORKS
| | Detox | Maestro |
|---|---|---|
| Type | Gray-box (syncs RN bridge) | Black-box |
| Config | JS/TS | YAML |
| Setup | Higher (build config) | Lower |
| Best | Deep RN integration | Quick setup, readable |
