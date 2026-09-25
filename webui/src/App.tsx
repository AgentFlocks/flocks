import { BrowserRouter } from 'react-router-dom'
import { Routes } from './routes'
import { ToastProvider } from './components/common/Toast'
import { ConfirmProvider } from './components/common/ConfirmDialog'
import { BackendStatusBanner } from './components/common/BackendStatusBanner'
import { AuthProvider } from './contexts/AuthContext'
import { ProductNameProvider } from './contexts/ProductNameContext'
import { ThemeProvider } from './contexts/ThemeContext'
import { DialogFocusProvider } from './contexts/DialogFocusContext'

export default function App() {
  return (
    <DialogFocusProvider>
      <ToastProvider>
        <ConfirmProvider>
          <ThemeProvider>
            <ProductNameProvider>
              <BrowserRouter>
                <AuthProvider>
                  <BackendStatusBanner />
                  <Routes />
                </AuthProvider>
              </BrowserRouter>
            </ProductNameProvider>
          </ThemeProvider>
        </ConfirmProvider>
      </ToastProvider>
    </DialogFocusProvider>
  )
}
