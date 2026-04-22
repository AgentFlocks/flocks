import { BrowserRouter } from 'react-router-dom'
import { Routes } from './routes'
import { ToastProvider } from './components/common/Toast'
import { ConfirmProvider } from './components/common/ConfirmDialog'
import { BackendStatusBanner } from './components/common/BackendStatusBanner'

export default function App() {
  return (
    <ToastProvider>
      <ConfirmProvider>
        <BrowserRouter>
          <BackendStatusBanner />
          <Routes />
        </BrowserRouter>
      </ConfirmProvider>
    </ToastProvider>
  )
}
