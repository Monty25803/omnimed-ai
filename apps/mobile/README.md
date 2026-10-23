# mobile

Expo (SDK 51, React Native 0.74) app for the roles that work away from a desk.

| Screen | Persona | Features |
|---|---|---|
| Nurse ward | Nurse | Ward list with early-warning scores, tasks, camera barcode eMAR with wrong-patient blocking |
| MR field | Medical rep | GPS check-in at the clinic, e-detailing with slide engagement tracking, end visit |
| Doctor alerts | Doctor | Open and escalated critical alerts with acknowledgement |

Tokens are kept in the device keychain via `expo-secure-store`. Alerts are polled every 10 seconds while the app is
in the foreground, because React Native has no built-in `EventSource` and polling copes better with flaky mobile
networks.

## Run

Start the compose stack first, then:

```bash
npm install
npm run android    # or: npm run ios, or npm start and scan the QR code with Expo Go
```

The `extra` block in `app.json` sets the backend addresses. The defaults (`10.0.2.2`) reach the host machine from
the Android emulator; for a physical device, use your computer's LAN address:

```json
"extra": {
  "gatewayUrl": "http://192.168.1.20:8000",
  "keycloakUrl": "http://192.168.1.20:8080"
}
```

Sign in with any persona from the main README (password `omnimed`). Camera and location permissions are requested
on first use.

```bash
npm run typecheck
```
