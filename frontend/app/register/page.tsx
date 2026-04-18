import RegisterForm from '@/components/auth/RegisterForm'

export const metadata = {
  title: 'Create Account — QoSentry',
}

export default function RegisterPage() {
  return (
    <div className="min-h-screen bg-background flex items-center justify-center px-4 py-16 relative">
      {/* Background decoration */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_60%_50%_at_50%_0%,rgba(99,102,241,0.1),transparent)]" />
      <div className="relative z-10 w-full flex justify-center">
        <RegisterForm />
      </div>
    </div>
  )
}
