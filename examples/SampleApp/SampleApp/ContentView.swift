import SwiftUI

struct ContentView: View {
    let page: Int

    var body: some View {
        VStack {
            Image(.appLogo)                       // Xcode 15+ generated symbol
            Image("hero_banner")                  // string literal
            Image("onboarding_\(page)")           // dynamic name -> keeps onboarding_*
            // Image("unused_promo")              // comments do not count
        }
    }
}

enum Banner: String {
    case copy = "hero_banner_copy"                // indirect reference via enum
}
