import { ReactNode } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, TextInputProps, View, ViewStyle } from "react-native";

export const colors = { brand: "#0f4c5c", brand2: "#127a8a", bg: "#f4f6f8", ink: "#1b2430", muted: "#5d6b7a", line: "#dde3ea", ok: "#1d7a46", warn: "#a86400", bad: "#b00020" };

export function Button({ title, onPress, tone = "brand", disabled, busy, small }: { title: string; onPress: () => void; tone?: "brand" | "bad" | "ghost"; disabled?: boolean; busy?: boolean; small?: boolean }) {
  const bg = tone === "bad" ? colors.bad : tone === "ghost" ? "transparent" : colors.brand2;
  return (
    <Pressable accessibilityRole="button" onPress={onPress} disabled={disabled || busy}
      style={({ pressed }) => [s.btn, small && s.btnSmall, { backgroundColor: bg, opacity: disabled ? 0.5 : pressed ? 0.8 : 1, borderColor: tone === "ghost" ? colors.brand2 : bg }]}>
      {busy ? <ActivityIndicator color="#fff" /> : <Text style={[s.btnText, tone === "ghost" && { color: colors.brand2 }, small && { fontSize: 13 }]}>{title}</Text>}
    </Pressable>
  );
}

export function Card({ title, children, style }: { title?: string; children: ReactNode; style?: ViewStyle }) {
  return (
    <View style={[s.card, style]}>
      {title ? <Text style={s.cardTitle}>{title}</Text> : null}
      {children}
    </View>
  );
}

export function Field(props: TextInputProps & { label: string }) {
  return (
    <View style={{ marginBottom: 8, flex: 1 }}>
      <Text style={s.label}>{props.label}</Text>
      <TextInput placeholderTextColor={colors.muted} {...props} style={[s.input, props.style]} />
    </View>
  );
}

export function Badge({ text, tone = "neutral" }: { text: string; tone?: "neutral" | "ok" | "warn" | "bad" }) {
  const c = tone === "ok" ? colors.ok : tone === "warn" ? colors.warn : tone === "bad" ? colors.bad : colors.muted;
  return <Text style={[s.badge, { color: c, borderColor: c }]}>{text}</Text>;
}

export function Banner({ text, tone }: { text: string; tone: "ok" | "bad" | "warn" }) {
  const c = tone === "ok" ? colors.ok : tone === "warn" ? colors.warn : colors.bad;
  return <Text style={[s.banner, { color: c, borderColor: c }]}>{text}</Text>;
}

export const s = StyleSheet.create({
  btn: { paddingVertical: 11, paddingHorizontal: 14, borderRadius: 8, alignItems: "center", borderWidth: 1, marginVertical: 4 },
  btnSmall: { paddingVertical: 6, paddingHorizontal: 10 },
  btnText: { color: "#fff", fontWeight: "600", fontSize: 15 },
  card: { backgroundColor: "#fff", borderRadius: 10, borderWidth: 1, borderColor: colors.line, padding: 12, marginBottom: 10 },
  cardTitle: { fontSize: 16, fontWeight: "700", color: colors.ink, marginBottom: 6 },
  label: { fontSize: 12, color: colors.muted, marginBottom: 2 },
  input: { borderWidth: 1, borderColor: colors.line, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 8, fontSize: 15, color: colors.ink, backgroundColor: "#fff" },
  badge: { borderWidth: 1, borderRadius: 99, paddingHorizontal: 8, paddingVertical: 1, fontSize: 11, fontWeight: "700", alignSelf: "flex-start", overflow: "hidden" },
  banner: { borderWidth: 1, borderRadius: 8, padding: 8, marginVertical: 6, fontSize: 14 },
  row: { flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap" },
  muted: { color: colors.muted, fontSize: 12 },
  text: { color: colors.ink, fontSize: 14 },
});
