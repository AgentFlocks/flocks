import { useTheme } from "../context/theme"

export interface TodoItemProps {
  status: string
  content: string
  activeForm?: string
}

export function TodoItem(props: TodoItemProps) {
  const { theme } = useTheme()
  const displayText = props.status === "in_progress" ? (props.activeForm || props.content) : props.content

  return (
    <box flexDirection="row" gap={0}>
      <text
        flexShrink={0}
        style={{
          fg: props.status === "in_progress" ? theme.warning : theme.textMuted,
        }}
      >
        [{props.status === "completed" ? "✓" : props.status === "in_progress" ? "•" : " "}]{" "}
      </text>
      <text
        flexGrow={1}
        wrapMode="word"
        style={{
          fg: props.status === "in_progress" ? theme.warning : theme.textMuted,
        }}
      >
        {displayText}
      </text>
    </box>
  )
}
