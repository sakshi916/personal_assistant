import MarkdownUI
import SwiftUI

struct MarkdownMessage: View {
    let markdown: String

    var body: some View {
        Markdown(markdown)
            .markdownBlockStyle(\.image) { (_: BlockConfiguration) in EmptyView() }
            .textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}
