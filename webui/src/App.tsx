import { BrowserRouter } from 'react-router-dom'
import { Routes } from './routes'
import { ToastProvider } from './components/common/Toast'
import { ConfirmProvider } from './components/common/ConfirmDialog'
import { BackendStatusBanner } from './components/common/BackendStatusBanner'
import { AuthProvider } from './contexts/AuthContext'
import { ProductNameProvider } from './contexts/ProductNameContext'
import { ThemeProvider } from './contexts/ThemeContext'

export default function App() {
  return (
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
  )
}
