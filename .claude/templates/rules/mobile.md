---
source: framework
---

## React Native / Expo

- `React.memo()` for static-prop components. No anonymous functions in `renderItem`.
- Optimize FlatList: `removeClippedSubviews`, `maxToRenderPerBatch`, `windowSize`.
- Expo Router for file-based routing. Never mix React Navigation + Expo Router.
- `react-native-encrypted-storage` for tokens/credentials. Never AsyncStorage for secrets.
- E2E tests with Detox for critical flows. Unit tests alone miss platform-specific bugs.
